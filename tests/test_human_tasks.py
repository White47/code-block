"""Narrow human-task facts require structural and binding witnesses."""

import unittest

from tests.test_training import analyze_files

from codeviz.analysis.human_tasks import ArtifactAccessKind, StaticTaskKind
from codeviz.core import Confidence, NodeKind


class HumanTaskAnalysisTests(unittest.TestCase):
    def test_gradient_accumulation_requires_same_bound_scale_and_backward(self):
        positive = analyze_files({
            "train.py": (
                "def step(loss, accumulation_steps):\n"
                "    for _ in range(accumulation_steps):\n"
                "        scaled = loss / accumulation_steps\n"
                "        scaled.backward()\n"
            ),
        })
        negative = analyze_files({
            "train.py": (
                "def step(loss, accumulation_steps, other_steps):\n"
                "    for _ in range(accumulation_steps):\n"
                "        scaled = loss / other_steps\n"
                "        scaled.backward()\n"
            ),
        })

        task, = positive.human_tasks.tasks
        owner = positive.symbol_index.entries[task.owner_symbol_id]
        control = positive.flow.graph.nodes[task.control_node_id]
        self.assertIs(task.kind, StaticTaskKind.GRADIENT_ACCUMULATION)
        self.assertEqual(owner.qualified_name, "train.step")
        self.assertIs(control.kind, NodeKind.CONTROL_BLOCK)
        self.assertEqual(control.metadata.get("statement_type"), "For")
        self.assertEqual(len(task.callsite_ids), 1)
        self.assertIs(task.confidence, Confidence.PROBABLE)
        self.assertFalse(any(item.kind is StaticTaskKind.GRADIENT_ACCUMULATION
                             for item in negative.human_tasks.tasks))

    def test_generation_facts_require_local_data_use_sampling_and_stdout(self):
        positive = analyze_files({
            "generate.py": (
                "def run(tokenizer, model, text, probs, tokens, steps):\n"
                "    encoded = tokenizer.encode(text)\n"
                "    tokens = model.generate(encoded)\n"
                "    for _ in range(steps):\n"
                "        sampled = torch.multinomial(probs, 1)\n"
                "        tokens = torch.cat((tokens, sampled), dim=1)\n"
                "    print(tokenizer.decode(tokens))\n"
            ),
        })
        negative = analyze_files({
            "helpers.py": (
                "def names_only(codec, value):\n"
                "    encoded = codec.encode(value)\n"
                "    return codec.decode(encoded)\n"
            ),
        })

        kinds = {item.kind for item in positive.human_tasks.tasks}
        self.assertEqual(kinds, {
            StaticTaskKind.INPUT_ENCODING,
            StaticTaskKind.TOKEN_SAMPLING,
            StaticTaskKind.OUTPUT_DECODING,
        })
        entry, = positive.human_tasks.entries
        self.assertEqual(entry.kind.value, "generation")
        self.assertEqual(len(entry.evidence_fact_ids), 3)
        self.assertTrue(all(item.confidence is Confidence.PROBABLE
                            for item in positive.human_tasks.tasks))
        self.assertEqual(negative.human_tasks.tasks, ())
        self.assertEqual(negative.human_tasks.entries, ())

    def test_model_data_path_proves_residual_merge_and_last_token_selection(self):
        analysis = analyze_files({
            "model.py": (
                "import torch.nn as nn\n"
                "class Net(nn.Module):\n"
                "    def __init__(self):\n"
                "        self.proj = nn.Linear(4, 4)\n"
                "    def forward(self, x):\n"
                "        logits = self.proj(x)\n"
                "        x = x + self.proj(x)\n"
                "        tail = logits[:, -1, :]\n"
                "        return x, tail\n"
            ),
        })

        kinds = {item.kind for item in analysis.human_tasks.tasks}
        self.assertIn(StaticTaskKind.RESIDUAL_MERGE, kinds)
        self.assertIn(StaticTaskKind.LAST_TOKEN_SELECTION, kinds)
        selected = [item for item in analysis.human_tasks.tasks if item.kind in {
            StaticTaskKind.RESIDUAL_MERGE, StaticTaskKind.LAST_TOKEN_SELECTION,
        }]
        self.assertTrue(all(item.callsite_ids and item.target_symbol_ids for item in selected))

    def test_evaluation_and_learning_rate_rules_need_structural_witnesses(self):
        analysis = analyze_files({
            "train.py": (
                "import torch\n"
                "@torch.no_grad()\n"
                "def evaluate(model, loss_fn, batch):\n"
                "    model.eval()\n"
                "    logits = model(batch)\n"
                "    loss = loss_fn(logits)\n"
                "    model.train()\n"
                "    return loss\n"
                "def update(optimizer, rate):\n"
                "    for group in optimizer.param_groups:\n"
                "        group['lr'] = rate\n"
            ),
        })

        kinds = {item.kind for item in analysis.human_tasks.tasks}
        self.assertIn(StaticTaskKind.EVALUATION_HELPER, kinds)
        self.assertIn(StaticTaskKind.LEARNING_RATE_UPDATE, kinds)

    def test_dynamic_artifact_keys_stay_owner_scoped(self):
        analysis = analyze_files({
            "data.py": (
                "import numpy as np\n"
                "import os\n"
                "def train_data(root):\n"
                "    return np.memmap(os.path.join(root, 'tokens.bin'), mode='r')\n"
                "def validation_data(root):\n"
                "    return np.memmap(os.path.join(root, 'tokens.bin'), mode='r')\n"
            ),
        })

        self.assertEqual(len(analysis.human_tasks.artifacts), 2)
        self.assertEqual(len({item.artifact_key for item in analysis.human_tasks.artifacts}), 2)
        self.assertTrue(all(item.artifact_key.startswith("dynamic:")
                            for item in analysis.human_tasks.artifacts))
        self.assertEqual(analysis.human_tasks.entries, ())

    def test_numpy_array_tofile_is_a_probable_write_and_preparation_entry(self):
        analysis = analyze_files({
            "prepare.py": (
                "import numpy as np\n"
                "values = np.array([1, 2, 3])\n"
                "values.tofile('tokens.bin')\n"
            ),
        })

        artifact, = analysis.human_tasks.artifacts
        entry, = analysis.human_tasks.entries
        self.assertIs(artifact.kind, ArtifactAccessKind.WRITE)
        self.assertEqual(artifact.artifact_key, "tokens.bin")
        self.assertIs(artifact.confidence, Confidence.PROBABLE)
        self.assertEqual(entry.evidence_fact_ids, (artifact.id,))
        self.assertEqual(entry.kind.value, "data_preparation")

    def test_artifact_lookalikes_and_rebound_tofile_receivers_are_rejected(self):
        custom = analyze_files({
            "custom.py": (
                "def memmap(path, mode): return path\n"
                "def write(): return memmap('tokens.bin', mode='w+')\n"
            ),
        })
        rebound = analyze_files({
            "prepare.py": (
                "import numpy as np\n"
                "values = np.array([1, 2, 3])\n"
                "values = object()\n"
                "values.tofile('tokens.bin')\n"
            ),
        })

        self.assertEqual(custom.human_tasks.artifacts, ())
        self.assertEqual(rebound.human_tasks.artifacts, ())


if __name__ == "__main__":
    unittest.main()
