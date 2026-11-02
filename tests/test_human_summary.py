"""Persisted-fact projections for functional architecture and task flows."""

import json
import unittest
from dataclasses import replace

from tests.test_training import analyze_files

from codeviz.core import Confidence
from codeviz.persistence.codec import decode_analysis, encode_analysis
from codeviz.views import SummaryRelationKind, build_human_summary


class HumanSummaryTests(unittest.TestCase):
    def test_generic_statistics_flow_folds_primitives_into_three_human_steps(self):
        analysis = analyze_files({
            "report.py": (
                "def load_values():\n"
                "    \"\"\"Read values.\"\"\"\n"
                "    return [1, 2, 3]\n"
                "def main():\n"
                "    values = load_values()\n"
                "    count = len(values)\n"
                "    total = sum(values)\n"
                "    print(total, count)\n"
            ),
        })

        result = build_human_summary(analysis)

        flow = result.flows[0]
        self.assertEqual(
            [step.title for step in flow.steps],
            ["Read values.", "Calculate statistics", "Display output"],
        )
        self.assertEqual(
            [relation.kind for relation in flow.relations],
            [SummaryRelationKind.CONTROL_NEXT, SummaryRelationKind.CONTROL_NEXT],
        )
        load = next(entry for entry in analysis.symbol_index.entries.values()
                    if entry.qualified_name == "report.load_values")
        self.assertEqual(flow.steps[0].owner_symbol_ids[0], load.id)
        self.assertEqual(flow.steps[0].basis, "source_author_statement")
        self.assertIs(flow.steps[0].confidence, Confidence.PROBABLE)
        graph_node_ids = set(analysis.flow.graph.nodes)
        self.assertTrue(all(
            set(step.code_node_ids) <= graph_node_ids for step in flow.steps
        ))

    def test_repeated_builtin_output_is_one_result_with_all_source_locations(self):
        analysis = analyze_files({
            "app.py": (
                "def main():\n"
                "    print('starting')\n"
                "    print('working')\n"
                "    print('finished')\n"
            ),
        })

        flow = build_human_summary(analysis).flows[0]

        self.assertEqual([step.title for step in flow.steps], ["Display output"])
        self.assertEqual(flow.steps[0].source_location_count, 3)
        self.assertEqual(len(flow.steps[0].code_node_ids), 3)

    def test_generic_flow_preserves_cfg_branch_and_loop_relations(self):
        analysis = analyze_files({
            "app.py": (
                "def accept():\n"
                "    \"\"\"Accept the item.\"\"\"\n"
                "def reject():\n"
                "    \"\"\"Reject the item.\"\"\"\n"
                "def main(flag, items):\n"
                "    if flag:\n"
                "        accept()\n"
                "    else:\n"
                "        reject()\n"
                "    for item in items:\n"
                "        accept()\n"
            ),
        })

        flow = build_human_summary(analysis).flows[0]

        self.assertEqual(
            {step.kind for step in flow.steps if step.kind in ("decision", "loop")},
            {"decision", "loop"},
        )
        kinds = {relation.kind for relation in flow.relations}
        self.assertTrue({
            SummaryRelationKind.BRANCH_TRUE,
            SummaryRelationKind.BRANCH_FALSE,
            SummaryRelationKind.LOOP_BACK,
        } <= kinds)
        self.assertTrue(all(step.evidence_ids for step in flow.steps))

    def test_interleaved_local_calls_keep_occurrences_and_author_evidence(self):
        analysis = analyze_files({
            "app.py": (
                "def first():\n"
                "    \"\"\"Prepare input.\"\"\"\n"
                "def second():\n"
                "    \"\"\"Transform input.\"\"\"\n"
                "def main():\n"
                "    first()\n"
                "    second()\n"
                "    first()\n"
            ),
        })

        flow = build_human_summary(analysis).flows[0]

        self.assertEqual(
            [step.title for step in flow.steps],
            ["Prepare input.", "Transform input.", "Prepare input."],
        )
        first_fact = next(
            fact for fact in analysis.human_understanding.author_statements
            if fact.text == "Prepare input."
        )
        self.assertTrue(all(
            first_fact.id in step.evidence_ids
            for step in (flow.steps[0], flow.steps[2])
        ))

    def test_architecture_keeps_module_and_parameter_sharing_distinct(self):
        analysis = analyze_files({
            "model.py": (
                "import torch.nn as nn\n"
                "class Net(nn.Module):\n"
                "    def __init__(self):\n"
                "        self.left = nn.Linear(4, 4)\n"
                "        self.right = self.left\n"
                "        self.embedding = nn.Embedding(8, 4)\n"
                "        self.head = nn.Linear(4, 8, bias=False)\n"
                "        self.embedding.weight = self.head.weight\n"
            ),
        })

        architecture = build_human_summary(analysis).architecture

        self.assertIsNotNone(architecture)
        shared = [relation for relation in architecture.relations
                  if relation.kind is SummaryRelationKind.SHARED]
        self.assertEqual(
            {(relation.sharing_kind, relation.sharing_attributes) for relation in shared},
            {("module_instance", None), ("parameter", ("weight", "weight"))},
        )
        area_ids = {area.id for area in architecture.areas}
        self.assertTrue(all(
            relation.source in area_ids and relation.target in area_ids
            for relation in shared
        ))
        graph_ids = set(analysis.flow.graph.nodes)
        self.assertTrue(all(
            set(area.code_node_ids) <= graph_ids for area in architecture.areas
        ))

    def test_generation_entry_projects_static_tasks_without_inventing_purpose(self):
        analysis = analyze_files({
            "model.py": (
                "import torch\n"
                "import torch.nn as nn\n"
                "class Net(nn.Module):\n"
                "    def generate(self, probs, tokens, steps):\n"
                "        for _ in range(steps):\n"
                "            sampled = torch.multinomial(probs, 1)\n"
                "            tokens = torch.cat((tokens, sampled), dim=1)\n"
                "        return tokens\n"
            ),
            "generate.py": (
                "from model import Net\n"
                "def run(tokenizer, text, probs, tokens, steps):\n"
                "    model = Net()\n"
                "    encoded = tokenizer.encode(text)\n"
                "    tokens = model.generate(probs, encoded, steps)\n"
                "    print(tokenizer.decode(tokens))\n"
            ),
        })

        summary = build_human_summary(analysis)

        flow = next(flow for flow in summary.flows if flow.title == "Generate output")
        self.assertEqual(
            [step.title for step in flow.steps],
            ["Encode input", "Sample next token", "Decode and display output"],
        )
        self.assertEqual(
            [step.basis for step in flow.steps],
            [
                "static_task:input_encoding",
                "static_task:token_sampling",
                "static_task:output_decoding",
            ],
        )
        current_scope_ids = set(analysis.symbol_index.entries)
        graph_ids = set(analysis.flow.graph.nodes)
        self.assertTrue(all(set(step.owner_symbol_ids) <= current_scope_ids
                            for step in flow.steps))
        self.assertTrue(all(set(step.code_node_ids) <= graph_ids for step in flow.steps))
        by_title = {step.title: step.id for step in flow.steps}
        self.assertIn((
            by_title["Sample next token"], by_title["Decode and display output"],
        ), {(relation.source, relation.target) for relation in flow.relations})

    def test_training_contains_relations_require_lexical_cfg_parents(self):
        analysis = analyze_files({
            "model.py": (
                "import torch.nn as nn\n"
                "class Net(nn.Module):\n"
                "    def forward(self, x): return x\n"
            ),
            "run.py": (
                "import torch\n"
                "import torch.nn as nn\n"
                "from model import Net\n"
                "def train(evaluate, save, batch, target, accumulation_steps):\n"
                "    model = Net()\n"
                "    optimizer = torch.optim.SGD(model.parameters(), lr=0.1)\n"
                "    criterion = nn.CrossEntropyLoss()\n"
                "    while True:\n"
                "        if evaluate:\n"
                "            if save:\n"
                "                torch.save(model, 'checkpoint.pt')\n"
                "        for micro in range(accumulation_steps):\n"
                "            prediction = model(batch)\n"
                "            loss = criterion(prediction, target)\n"
                "            loss = loss / accumulation_steps\n"
                "            loss.backward()\n"
                "        optimizer.step()\n"
                "        optimizer.zero_grad()\n"
            ),
        })

        flow = next(flow for flow in build_human_summary(analysis).flows
                    if flow.title == "Train model")
        by_title = {step.title: step.id for step in flow.steps}
        contains = {
            (relation.source, relation.target)
            for relation in flow.relations
            if relation.kind is SummaryRelationKind.CONTAINS
        }

        self.assertIn((by_title["Iteration loop"], by_title["Condition: evaluate"]),
                      contains)
        self.assertIn((by_title["Condition: evaluate"], by_title["Condition: save"]),
                      contains)
        self.assertNotIn((by_title["Accumulate gradients"],
                          by_title["Save training checkpoint"]), contains)

    def test_training_loop_contains_static_tasks(self):
        analysis = analyze_files({
            "model.py": (
                "import torch.nn as nn\n"
                "class Net(nn.Module):\n"
                "    def forward(self, x): return x\n"
            ),
            "run.py": (
                "import torch\n"
                "import torch.nn as nn\n"
                "from model import Net\n"
                "def train(batch, target, accumulation_steps):\n"
                "    model = Net()\n"
                "    optimizer = torch.optim.SGD(model.parameters(), lr=0.1)\n"
                "    criterion = nn.CrossEntropyLoss()\n"
                "    while True:\n"
                "        for group in optimizer.param_groups:\n"
                "            group['lr'] = 0.01\n"
                "        for micro in range(accumulation_steps):\n"
                "            prediction = model(batch)\n"
                "            loss = criterion(prediction, target)\n"
                "            loss = loss / accumulation_steps\n"
                "            loss.backward()\n"
                "        optimizer.step()\n"
                "        optimizer.zero_grad()\n"
            ),
        })

        flow = next(flow for flow in build_human_summary(analysis).flows
                    if flow.title == "Train model")
        by_title = {step.title: step.id for step in flow.steps}
        contains = {
            (relation.source, relation.target)
            for relation in flow.relations
            if relation.kind is SummaryRelationKind.CONTAINS
        }

        self.assertIn((by_title["Iteration loop"],
                       by_title["Update learning rate"]), contains)
        self.assertIn((by_title["Iteration loop"],
                       by_title["Accumulate gradients"]), contains)
        self.assertIn((by_title["Accumulate gradients"],
                       by_title["Run model forward"]), contains)
        self.assertIn((by_title["Accumulate gradients"],
                       by_title["Backpropagate loss"]), contains)

    def test_training_flow_uses_persisted_predecessors_not_entry_tuple_order(self):
        analysis = analyze_files({
            "model.py": (
                "import torch.nn as nn\n"
                "class Net(nn.Module):\n"
                "    def forward(self, x): return x\n"
            ),
            "run.py": (
                "import torch\n"
                "import torch.nn as nn\n"
                "from model import Net\n"
                "def train(loader):\n"
                "    model = Net()\n"
                "    optimizer = torch.optim.SGD(model.parameters(), lr=0.1)\n"
                "    criterion = nn.CrossEntropyLoss()\n"
                "    model.train()\n"
                "    for batch, target in loader:\n"
                "        optimizer.zero_grad()\n"
                "        prediction = model(batch)\n"
                "        loss = criterion(prediction, target)\n"
                "        loss.backward()\n"
                "        optimizer.step()\n"
            ),
        })
        original = build_human_summary(analysis)
        entry = analysis.training.entry_points[0]
        scrambled = replace(
            analysis,
            training=replace(
                analysis.training,
                entry_points=(replace(entry, flow_node_ids=tuple(
                    reversed(entry.flow_node_ids)
                )),),
            ),
        )

        second = build_human_summary(scrambled)

        flow = next(flow for flow in original.flows if flow.title == "Train model")
        self.assertEqual(second.flows, original.flows)
        by_title = {step.title: step.id for step in flow.steps}
        relations = {(item.source, item.target, item.kind) for item in flow.relations}
        self.assertIn((
            by_title["Clear gradients"], by_title["Run model forward"],
            SummaryRelationKind.CONTROL_NEXT,
        ), relations)
        self.assertIn((
            by_title["Update parameters"], by_title["Iterate over batches"],
            SummaryRelationKind.LOOP_BACK,
        ), relations)

    def test_architecture_uses_only_resolved_local_calls_for_static_relations(self):
        analysis = analyze_files({
            "app.py": (
                "def load():\n"
                "    \"\"\"Read records.\"\"\"\n"
                "def main():\n"
                "    load()\n"
                "    print(len([]))\n"
            ),
        })

        architecture = build_human_summary(analysis).architecture

        static_calls = [relation for relation in architecture.relations
                        if relation.kind is SummaryRelationKind.STATIC_CALL]
        self.assertEqual(len(static_calls), 1)
        area_ids = {area.id for area in architecture.areas}
        self.assertIn(static_calls[0].source, area_ids)
        self.assertIn(static_calls[0].target, area_ids)
        self.assertIsNone(static_calls[0].sharing_kind)

    def test_model_flow_reports_configured_repetition_not_runtime_execution_count(self):
        analysis = analyze_files({
            "model.py": (
                "import torch.nn as nn\n"
                "class Block(nn.Module):\n"
                "    def forward(self, x): return x\n"
                "class Net(nn.Module):\n"
                "    def __init__(self):\n"
                "        self.embedding = nn.Embedding(8, 4)\n"
                "        self.blocks = nn.ModuleList([Block() for _ in range(3)])\n"
                "        self.norm = nn.LayerNorm(4)\n"
                "    def forward(self, x):\n"
                "        return self.norm(self.embedding(x))\n"
            ),
        })

        summary = build_human_summary(analysis)

        flow = next(flow for flow in summary.flows if flow.title == "Model structure")
        self.assertTrue(any("3 configured" in step.title for step in flow.steps))
        self.assertTrue(all(
            step.basis.startswith(("model:", "static_task:")) for step in flow.steps
        ))
        self.assertTrue(any(
            relation.kind is SummaryRelationKind.MODEL_USE
            for relation in summary.architecture.relations
        ))
        graph_ids = set(analysis.flow.graph.nodes)
        self.assertTrue(all(set(step.code_node_ids) <= graph_ids for step in flow.steps))

    def test_model_instances_keep_real_names_with_neutral_embedding_title(self):
        analysis = analyze_files({
            "model.py": (
                "import torch.nn as nn\n"
                "class GPT(nn.Module):\n"
                "    def __init__(self):\n"
                "        self.wte = nn.Embedding(8, 4)\n"
                "        self.wpe = nn.Embedding(8, 4)\n"
                "        self.lm_head = nn.Linear(4, 8, bias=False)\n"
                "    def forward(self, idx):\n"
                "        return self.lm_head(self.wte(idx) + self.wpe(idx))\n"
            ),
        })
        instances = {item.name: item for item in analysis.model.instances}
        flow = next(flow for flow in build_human_summary(analysis).flows
                    if any(instances["wte"].id in step.evidence_ids
                           for step in flow.steps))
        flow_steps = {
            evidence: step for step in flow.steps for evidence in step.evidence_ids
        }
        architecture = build_human_summary(analysis).architecture
        areas = {
            evidence: step for step in architecture.areas
            for evidence in step.evidence_ids
        }

        self.assertEqual(flow_steps[instances["wte"].id].code_name, "wte")
        self.assertEqual(flow_steps[instances["wpe"].id].code_name, "wpe")
        self.assertEqual(flow_steps[instances["lm_head"].id].code_name, "lm_head")
        self.assertEqual(areas[instances["wte"].id].code_name, "wte")
        self.assertEqual(areas[instances["wpe"].id].code_name, "wpe")
        self.assertEqual(
            flow_steps[instances["wte"].id].title,
            "Look up embedding vectors",
        )
        self.assertEqual(
            flow_steps[instances["wpe"].id].title,
            "Look up embedding vectors",
        )
        self.assertEqual(
            flow_steps[instances["wte"].id].owner_symbol_ids,
            flow_steps[instances["wpe"].id].owner_symbol_ids,
        )
        with self.assertRaises(ValueError):
            replace(flow_steps[instances["wte"].id], code_name="")

    def test_local_layer_names_do_not_inherit_framework_purposes(self):
        analysis = analyze_files({
            "model.py": (
                "import torch.nn as nn\n"
                "import vendor\n"
                "class Embedding(nn.Module):\n"
                "    def forward(self, value): return value\n"
                "class Attention(nn.Module):\n"
                "    def forward(self, value): return value\n"
                "class Net(nn.Module):\n"
                "    def __init__(self):\n"
                "        self.custom_embedding = Embedding()\n"
                "        self.custom_attention = Attention()\n"
                "        self.framework_embedding = nn.Embedding(8, 4)\n"
                "        self.vendor_embedding = vendor.Embedding()\n"
                "        self.vendor_attention = vendor.FakeAttention()\n"
                "    def forward(self, value):\n"
                "        value = self.vendor_embedding(value)\n"
                "        value = self.vendor_attention(value)\n"
                "        return self.custom_attention(self.custom_embedding(value))\n"
            ),
        })
        instances = {item.name: item for item in analysis.model.instances}
        flow = next(flow for flow in build_human_summary(analysis).flows
                    if any(instances["custom_embedding"].id in step.evidence_ids
                           for step in flow.steps))
        titles = {
            evidence: step.title
            for step in flow.steps for evidence in step.evidence_ids
        }

        self.assertEqual(
            titles[instances["custom_embedding"].id],
            "Model component: custom_embedding",
        )
        self.assertEqual(
            titles[instances["custom_attention"].id],
            "Model component: custom_attention",
        )
        self.assertEqual(
            titles[instances["framework_embedding"].id],
            "Look up embedding vectors",
        )
        self.assertEqual(
            titles[instances["vendor_embedding"].id],
            "Model component: vendor_embedding",
        )
        self.assertEqual(
            titles[instances["vendor_attention"].id],
            "Model component: vendor_attention",
        )

    def test_model_dict_and_single_member_list_are_not_reported_as_repetition(self):
        analysis = analyze_files({
            "model.py": (
                "import torch.nn as nn\n"
                "class Block(nn.Module):\n"
                "    def forward(self, x): return x\n"
                "class Net(nn.Module):\n"
                "    def __init__(self):\n"
                "        self.named = nn.ModuleDict({'only': Block()})\n"
                "        self.single = nn.ModuleList([Block()])\n"
                "    def forward(self, x): return self.named['only'](x)\n"
            ),
        })
        instances = {item.name: item for item in analysis.model.instances}
        flow = next(flow for flow in build_human_summary(analysis).flows
                    if any(instances["named"].id in step.evidence_ids
                           for step in flow.steps))
        titles = {
            evidence: step.title
            for step in flow.steps for evidence in step.evidence_ids
        }

        self.assertEqual(titles[instances["named"].id],
                         "Organize named model components")
        self.assertNotIn("Repeat", titles[instances["single"].id])

    def test_artifact_relation_requires_the_same_canonical_persisted_key(self):
        analysis = analyze_files({
            "data.py": (
                "def write_data():\n"
                "    return open('artifacts/tokens.bin', mode='wb')\n"
                "def read_data():\n"
                "    return open('artifacts/tokens.bin', mode='rb')\n"
                "def unrelated():\n"
                "    return open('other/tokens.bin', mode='rb')\n"
            ),
        })

        architecture = build_human_summary(analysis).architecture

        artifact_edges = [item for item in architecture.relations
                          if item.kind is SummaryRelationKind.DATA_ARTIFACT]
        self.assertEqual(len(artifact_edges), 1)
        facts = {fact.id: fact for fact in analysis.human_tasks.artifacts}
        evidence = [facts[item] for item in artifact_edges[0].evidence_ids]
        self.assertEqual({item.artifact_key for item in evidence},
                         {"artifacts/tokens.bin"})

    def test_unmatched_dynamic_artifact_accesses_are_an_explicit_gap(self):
        analysis = analyze_files({
            "data.py": (
                "import os\n"
                "def write_data(root):\n"
                "    return open(os.path.join(root, 'tokens.bin'), mode='wb')\n"
                "def read_data(root):\n"
                "    return open(os.path.join(root, 'tokens.bin'), mode='rb')\n"
            ),
        })

        architecture = build_human_summary(analysis).architecture

        self.assertFalse(any(
            relation.kind is SummaryRelationKind.DATA_ARTIFACT
            for relation in architecture.relations
        ))
        self.assertTrue(architecture.incomplete)
        self.assertGreater(architecture.omitted_count, 0)

    def test_custom_local_print_is_a_normal_call_while_builtin_output_is_retained(self):
        local = analyze_files({
            "app.py": (
                "def print(value):\n"
                "    \"\"\"Render a custom record.\"\"\"\n"
                "def main():\n"
                "    print('hello')\n"
            ),
        })
        hello = analyze_files({"pkg/__main__.py": "print('hello world')\n"})

        local_flow = build_human_summary(local).flows[0]
        hello_flow = build_human_summary(hello).flows[0]

        self.assertEqual([step.title for step in local_flow.steps],
                         ["Render a custom record."])
        self.assertEqual([step.title for step in hello_flow.steps], ["Display output"])
        self.assertEqual(hello_flow.steps[0].kind, "result")

    def test_cached_analysis_projects_the_same_summary_without_ast(self):
        analysis = analyze_files({
            "app.py": (
                "def work():\n"
                "    \"\"\"Perform the work.\"\"\"\n"
                "def main():\n"
                "    work()\n"
            ),
        })
        restored = decode_analysis(json.loads(json.dumps(encode_analysis(analysis))))

        self.assertTrue(all(parsed.tree is None for parsed in restored.files))
        self.assertEqual(build_human_summary(restored), build_human_summary(analysis))

    def test_training_flow_keeps_proven_gradient_accumulation_container(self):
        analysis = analyze_files({
            "model.py": (
                "import torch.nn as nn\n"
                "class Net(nn.Module):\n"
                "    def forward(self, x): return x\n"
            ),
            "run.py": (
                "import torch\n"
                "import torch.nn as nn\n"
                "from model import Net\n"
                "def train(batch, target, accumulation_steps):\n"
                "    model = Net()\n"
                "    optimizer = torch.optim.SGD(model.parameters(), lr=0.1)\n"
                "    criterion = nn.CrossEntropyLoss()\n"
                "    for micro in range(accumulation_steps):\n"
                "        prediction = model(batch)\n"
                "        loss = criterion(prediction, target)\n"
                "        scaled = loss / accumulation_steps\n"
                "        scaled.backward()\n"
                "    optimizer.step()\n"
                "    optimizer.zero_grad()\n"
            ),
        })

        flow = next(flow for flow in build_human_summary(analysis).flows
                    if flow.title == "Train model")

        accumulation = [step for step in flow.steps
                        if step.basis == "static_task:gradient_accumulation"]
        self.assertEqual(len(accumulation), 1)
        self.assertEqual(accumulation[0].title, "Accumulate gradients")
        self.assertEqual(accumulation[0].kind, "loop")

    def test_while_true_is_an_iteration_loop_and_decisions_show_persisted_conditions(self):
        analysis = analyze_files({
            "model.py": (
                "import torch.nn as nn\n"
                "class Net(nn.Module):\n"
                "    def forward(self, x): return x\n"
            ),
            "run.py": (
                "import torch\n"
                "from model import Net\n"
                "def train(flag, value):\n"
                "    model = Net()\n"
                "    optimizer = torch.optim.SGD([], lr=0.1)\n"
                "    while True:\n"
                "        output = model(value)\n"
                "        if flag:\n"
                "            optimizer.step()\n"
                "        optimizer.zero_grad()\n"
            ),
        })

        flow = next(flow for flow in build_human_summary(analysis).flows
                    if flow.title == "Train model")

        loop = next(step for step in flow.steps if step.kind == "loop")
        self.assertEqual((loop.title, loop.basis), ("Iteration loop", "unknown"))
        self.assertIn("Condition: flag", {
            step.title for step in flow.steps if step.kind == "decision"
        })

    def test_called_evaluation_helper_and_loss_follow_real_conditional_cfg(self):
        analysis = analyze_files({
            "model.py": (
                "import torch.nn as nn\n"
                "class Net(nn.Module):\n"
                "    def forward(self, x): return x\n"
            ),
            "run.py": (
                "import torch\n"
                "import torch.nn as nn\n"
                "from model import Net\n"
                "@torch.no_grad()\n"
                "def estimate_loss(model, criterion, batch):\n"
                "    model.eval()\n"
                "    logits = model(batch)\n"
                "    loss = criterion(logits)\n"
                "    model.train()\n"
                "    return loss\n"
                "def train(setup, flag, save, batch, target):\n"
                "    model = Net()\n"
                "    optimizer = torch.optim.SGD(model.parameters(), lr=0.1)\n"
                "    criterion = nn.CrossEntropyLoss()\n"
                "    if setup:\n"
                "        print('ready')\n"
                "    while True:\n"
                "        if flag:\n"
                "            metric = estimate_loss(model, criterion, batch)\n"
                "            if save:\n"
                "                if target is not None:\n"
                "                    torch.save(model, 'checkpoint.pt')\n"
                "        prediction = model(batch)\n"
                "        loss = criterion(prediction, target)\n"
                "        loss.backward()\n"
                "        optimizer.step()\n"
                "        optimizer.zero_grad()\n"
            ),
        })

        flow = next(flow for flow in build_human_summary(analysis).flows
                    if flow.title == "Train model")

        titles = [step.title for step in flow.steps]
        expected = [
            "Condition: flag",
            "Evaluate model",
            "Collect evaluation loss",
            "Save training checkpoint",
            "Run model forward",
        ]
        self.assertEqual([title for title in titles if title in expected], expected)
        self.assertNotIn("Condition: setup", titles)
        by_title = {step.title: step.id for step in flow.steps}
        relations = {(item.source, item.target, item.kind) for item in flow.relations}
        self.assertIn((
            by_title["Condition: flag"], by_title["Evaluate model"],
            SummaryRelationKind.BRANCH_TRUE,
        ), relations)
        self.assertIn((
            by_title["Evaluate model"], by_title["Collect evaluation loss"],
            SummaryRelationKind.CONTROL_NEXT,
        ), relations)
        self.assertTrue(any(
            source == by_title["Collect evaluation loss"]
            and target == by_title["Save training checkpoint"]
            for source, target, _ in relations
        ))
        for source, target in (
            ("Save training checkpoint", "Run model forward"),
            ("Run model forward", "Calculate training loss"),
            ("Calculate training loss", "Backpropagate loss"),
            ("Backpropagate loss", "Update parameters"),
            ("Update parameters", "Clear gradients"),
        ):
            with self.subTest(source=source, target=target):
                self.assertIn((
                    by_title[source], by_title[target],
                    SummaryRelationKind.CONTROL_NEXT,
                ), relations)


if __name__ == "__main__":
    unittest.main()
