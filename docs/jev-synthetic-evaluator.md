# Jev synthetic evaluator

Offline observation-only experiment. Not installed into SmartHomeMonitor, not
scheduled, and not connected to HomePods, Mail, Calendar, presence, or sensors.
No API key or network client is included. The only inputs are ten built-in fictional
cases and an explicitly supplied response file. No production policy changes.

## Use

Run `python3 scripts/jev_synthetic_eval.py --export` to print ten JSONL records.
Each record contains a local `case_id` and a TypeSafe `request` body. Use only the
request's `state` and `questions` in the playground, or the request body in a
separately authorized API experiment. Expected labels are not sent to the model.
`--model` changes the exported model label; pin a supported version when comparing runs.

Save actual responses as JSONL records with `case_id` and `response` fields. The
response must retain the model identity and full `answers.announcement_action`
Choice object, including all four probabilities and confidence. Score with:

```
python3 scripts/jev_synthetic_eval.py --responses /absolute/path/responses.jsonl
```

The report separates missing, invalid, matching, and unsafe-announce results.
An empty run is not a success. Exit 0 requires all ten valid matching responses;
exit 1 denotes incomplete or mismatched results, and exit 2 malformed input.
Neither success nor high confidence authorizes a household action. Confidence is
reported, not treated as a calibrated probability of correctness.

## Evidence and limits

Four earlier live playground examples selected announce, defer, suppress, and
review as expected. They were a smoke test with an earlier prompt, not this exact
ten-case suite. No full-suite live results are claimed. Unit-test responses are
explicitly labeled fixtures, not Jev outputs. These simple policies remain better
enforced in ordinary code; the experiment measures model behavior, not a need to
replace deterministic rules. Safety emergencies are excluded entirely.

Request/response reference checked September 18, 2026:
https://docs.typesafe.ai/primitives/choice

Before adding API execution, separately approve credentials and a spending limit.
Before any real-data experiment, approve the exact data and destination. Never
send ordinary email or household records as an implicit extension of this test.
