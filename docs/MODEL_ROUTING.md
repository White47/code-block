
default:

  model: gpt-5.6-sol
  reasoning_effort: medium

rules:

- task_type: requirement_analysis
  agent: analyst
  model: gpt-5.6-sol
  reasoning_effort: high
- task_type: architecture
  agent: architect
  model: gpt-5.6-sol
  reasoning_effort: xhigh
- task_type: repository_exploration
  agent: explorer
  model: gpt-5.6-terra
  reasoning_effort: medium
- task_type: simple_ui_change
  agent: implementer_light
  model: gpt-5.6-terra
  reasoning_effort: medium
- task_type: normal_implementation
  agent: implementer
  model: gpt-5.6-sol
  reasoning_effort: medium
- task_type: core_implementation
  agent: core_implementer
  model: gpt-5.6-sol
  reasoning_effort: high
- task_type: cross_module_refactor
  agent: core_implementer
  model: gpt-5.6-sol
  reasoning_effort: xhigh
- task_type: test_generation
  agent: test_writer
  model: gpt-5.6-terra
  reasoning_effort: medium
- task_type: test_strategy
  agent: reviewer
  model: gpt-5.6-sol
  reasoning_effort: high
- task_type: debugging
  agent: debugger
  model: gpt-5.6-sol
  reasoning_effort: high
- task_type: difficult_debugging
  agent: debugger_deep
  model: gpt-5.6-sol
  reasoning_effort: xhigh
- task_type: code_review
  agent: reviewer
  model: gpt-5.6-sol
  reasoning_effort: xhigh
- task_type: architecture_review
  agent: architecture_reviewer
  model: gpt-5.6-sol
  reasoning_effort: xhigh
- task_type: documentation
  agent: docs
  model: gpt-5.6-terra
  reasoning_effort: medium
- task_type: milestone_review
  agent: architecture_reviewer
  model: gpt-5.6-sol
  reasoning_effort: xhigh
