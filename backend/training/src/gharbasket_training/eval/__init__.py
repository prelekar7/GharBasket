"""The evaluation harness (docs/research/eval-protocol.md): simulator A/B over seeds for the
meal planner (RQ2), the pantry rule-table baseline and its cold-start curve (RQ1), an HTML and
JSON report in docs/eval, and `intel.model_metrics` rows through the API.

    just eval --seeds 7 11 13 17 19 --weeks 4
    just eval --smoke            # one seed, one week, no CP-SAT: the CI check
"""
