"""Framework training roles require receiver evidence, not method spelling."""

import ast
import unittest

import codeviz.semantic as semantic
from codeviz.core import Confidence


def call(source: str) -> ast.Call:
    node = ast.parse(source, mode="eval").body
    assert isinstance(node, ast.Call)
    return node


def matched_role(match):
    assert match is not None, "expected a training-rule match"
    return match.role


class TrainingRuleTests(unittest.TestCase):
    def test_optimizer_step_requires_receiver_evidence(self):
        rule_type = getattr(semantic, "PyTorchTrainingRule", None)
        self.assertIsNotNone(rule_type)
        rule = rule_type()
        unknown = rule.classify_call(call("worker.step()"), {}, {})
        self.assertIsNone(unknown)
        receiver = semantic.ReceiverFact("optimizer", Confidence.EXACT,
                                         ("assigned torch.optim.Adam",))
        matched = rule.classify_call(call("optimizer.step()"), {}, {"optimizer": receiver})
        self.assertEqual(matched.role, semantic.TrainingRole.PARAMETER_UPDATE)
        self.assertEqual(matched.confidence, Confidence.EXACT)
        self.assertIn("torch.optim.Adam", " ".join(matched.evidence))

    def test_model_mode_does_not_imply_no_grad(self):
        rule = semantic.PyTorchTrainingRule()
        model = semantic.ReceiverFact("model", Confidence.EXACT, ("Net constructor",))
        train = rule.classify_call(call("model.train()"), {}, {"model": model})
        evaluate = rule.classify_call(call("model.eval()"), {}, {"model": model})
        train_false = rule.classify_call(call("model.train(False)"), {}, {"model": model})
        no_grad = rule.classify_call(call("torch.no_grad()"), {"torch": "torch"}, {})
        self.assertEqual(matched_role(train), semantic.TrainingRole.TRAINING_MODE)
        self.assertEqual(matched_role(evaluate), semantic.TrainingRole.EVALUATION_MODE)
        self.assertEqual(matched_role(train_false), semantic.TrainingRole.EVALUATION_MODE)
        self.assertEqual(matched_role(no_grad), semantic.TrainingRole.DISABLE_GRADIENT)
        assert no_grad is not None
        self.assertEqual(no_grad.confidence, Confidence.EXACT)
        self.assertIsNone(rule.classify_call(call("torch.no_grad()"), {}, {}))

    def test_forward_loss_and_backward_keep_receiver_confidence(self):
        rule = semantic.PyTorchTrainingRule()
        facts = {
            "model": semantic.ReceiverFact("model", Confidence.EXACT, ("Net constructor",)),
            "criterion": semantic.ReceiverFact("criterion", Confidence.EXACT,
                                                ("nn.CrossEntropyLoss constructor",)),
            "loss": semantic.ReceiverFact("loss", Confidence.PROBABLE,
                                           ("name suggests loss",)),
        }
        self.assertEqual(matched_role(rule.classify_call(call("model(x)"), {}, facts)),
                         semantic.TrainingRole.FORWARD_PASS)
        self.assertEqual(matched_role(rule.classify_call(call("criterion(pred, target)"), {}, facts)),
                         semantic.TrainingRole.LOSS_CALCULATION)
        backward = rule.classify_call(call("loss.backward()"), {}, facts)
        self.assertEqual(matched_role(backward), semantic.TrainingRole.BACKWARD_PASS)
        assert backward is not None
        self.assertEqual(backward.confidence, Confidence.PROBABLE)
        self.assertIsNone(rule.classify_call(call("other.backward()"), {}, facts))

    def test_optimizer_scheduler_and_checkpoint_are_distinct(self):
        rule = semantic.PyTorchTrainingRule()
        facts = {
            "optimizer": semantic.ReceiverFact("optimizer", Confidence.EXACT,
                                                ("torch.optim.SGD",)),
            "scheduler": semantic.ReceiverFact("scheduler", Confidence.PROBABLE,
                                                ("scheduler parameter",)),
        }
        self.assertEqual(matched_role(rule.classify_call(call("optimizer.zero_grad()"), {}, facts)),
                         semantic.TrainingRole.CLEAR_GRADIENTS)
        lr = rule.classify_call(call("scheduler.step()"), {}, facts)
        self.assertEqual(matched_role(lr), semantic.TrainingRole.LR_UPDATE)
        assert lr is not None
        self.assertEqual(lr.confidence, Confidence.PROBABLE)
        self.assertEqual(matched_role(rule.classify_call(call("torch.save(model, path)"),
                                                         {"torch": "torch"}, {})),
                         semantic.TrainingRole.CHECKPOINT_SAVE)
        self.assertEqual(matched_role(rule.classify_call(call("torch.load(path)"),
                                                         {"torch": "torch"}, {})),
                         semantic.TrainingRole.CHECKPOINT_LOAD)
        self.assertEqual(rule.classify_call(call("torch.save(model, path)"),
                                            {"torch": "torch"}, {}).confidence,
                         Confidence.PROBABLE)


if __name__ == "__main__":
    unittest.main()
