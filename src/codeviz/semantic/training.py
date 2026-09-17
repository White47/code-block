"""Static training-role matching; never imports or executes PyTorch."""

import ast
from dataclasses import dataclass
from enum import StrEnum

from codeviz.core import Confidence

from .pytorch import PyTorchModelRule


class TrainingRole(StrEnum):
    TRAINING_MODE = "TrainingMode"
    EVALUATION_MODE = "EvaluationMode"
    FORWARD_PASS = "ForwardPass"
    LOSS_CALCULATION = "LossCalculation"
    CLEAR_GRADIENTS = "ClearGradients"
    BACKWARD_PASS = "BackwardPass"
    PARAMETER_UPDATE = "ParameterUpdate"
    LR_UPDATE = "LRUpdate"
    DISABLE_GRADIENT = "DisableGradient"
    EPOCH_LOOP = "EpochLoop"
    BATCH_LOOP = "BatchLoop"
    CHECKPOINT_SAVE = "CheckpointSave"
    CHECKPOINT_LOAD = "CheckpointLoad"


@dataclass(frozen=True, slots=True)
class ReceiverFact:
    role: str
    confidence: Confidence
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TrainingRuleMatch:
    role: TrainingRole
    confidence: Confidence
    evidence: tuple[str, ...]


class PyTorchTrainingRule:
    def classify_call(self, call: ast.Call, imports: dict[str, str],
                      receivers: dict[str, ReceiverFact]) -> TrainingRuleMatch | None:
        expression = call.func
        canonical = PyTorchModelRule().canonical_name(expression, imports)
        if canonical == "torch.no_grad":
            return TrainingRuleMatch(TrainingRole.DISABLE_GRADIENT, Confidence.EXACT,
                                     (f"explicit {canonical} import and call",))
        if canonical == "torch.save":
            return TrainingRuleMatch(TrainingRole.CHECKPOINT_SAVE, Confidence.PROBABLE,
                                     ("explicit torch.save call; checkpoint intent is inferred",))
        if canonical == "torch.load":
            return TrainingRuleMatch(TrainingRole.CHECKPOINT_LOAD, Confidence.PROBABLE,
                                     ("explicit torch.load call; checkpoint intent is inferred",))
        if isinstance(expression, ast.Name):
            receiver = receivers.get(expression.id)
            if receiver is not None and receiver.role in ("model", "criterion"):
                role = (TrainingRole.FORWARD_PASS if receiver.role == "model"
                        else TrainingRole.LOSS_CALCULATION)
                return TrainingRuleMatch(role, receiver.confidence,
                                         (*receiver.evidence, f"{receiver.role} called"))
            return None
        if not isinstance(expression, ast.Attribute) or not isinstance(expression.value, ast.Name):
            return None
        receiver = receivers.get(expression.value.id)
        if receiver is None:
            return None
        if receiver.role == "model" and expression.attr in ("train", "eval"):
            mode = TrainingRole.TRAINING_MODE if expression.attr == "train" else TrainingRole.EVALUATION_MODE
            confidence = receiver.confidence
            if expression.attr == "train" and (call.args or call.keywords):
                mode_arg = call.args[0] if call.args else next(
                    (keyword.value for keyword in call.keywords if keyword.arg == "mode"), None)
                if isinstance(mode_arg, ast.Constant) and type(mode_arg.value) is bool:
                    mode = (TrainingRole.TRAINING_MODE if mode_arg.value
                            else TrainingRole.EVALUATION_MODE)
                else:
                    confidence = Confidence.PROBABLE
            return TrainingRuleMatch(mode, confidence,
                                     (*receiver.evidence, f"model.{expression.attr}() syntax"))
        if receiver.role == "model" and expression.attr == "forward":
            return TrainingRuleMatch(TrainingRole.FORWARD_PASS, Confidence.PROBABLE,
                                     (*receiver.evidence, "direct forward() call may be overridden"))
        if receiver.role == "optimizer" and expression.attr == "zero_grad":
            return TrainingRuleMatch(TrainingRole.CLEAR_GRADIENTS, receiver.confidence,
                                     (*receiver.evidence, "optimizer.zero_grad() syntax"))
        if receiver.role == "optimizer" and expression.attr == "step":
            return TrainingRuleMatch(TrainingRole.PARAMETER_UPDATE, receiver.confidence,
                                     (*receiver.evidence, "optimizer.step() syntax"))
        if receiver.role == "scheduler" and expression.attr == "step":
            return TrainingRuleMatch(TrainingRole.LR_UPDATE, receiver.confidence,
                                     (*receiver.evidence, "scheduler.step() syntax"))
        if receiver.role == "loss" and expression.attr == "backward":
            return TrainingRuleMatch(TrainingRole.BACKWARD_PASS, receiver.confidence,
                                     (*receiver.evidence, "loss.backward() syntax"))
        return None
