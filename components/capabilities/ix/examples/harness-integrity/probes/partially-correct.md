---
function_name: merge
# basic is half the score, edge cases 0.3, refusing bad input 0.2: this answer passes the
# basic tier only, so its weighted score is 0.5 and 1 test of 3 passes.
tiers: {basic: 0.5, edge: 0.3, security: 0.2}
test_cases:
  - {input: [[[1, 3], [2, 6]]], expected: [[1, 6]], tier: basic, description: overlapping}
  - {input: [[[1, 4], [4, 5]]], expected: [[1, 5]], tier: edge, description: touching}
  - {input: ["not a list"], raises: TypeError, tier: security, description: refuses a string}
options:
  mock_response: |
    ```python
    def merge(intervals):
        merged = []
        for start, end in sorted(intervals):
            if merged and start < merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        return merged
    ```
---

Write a Python function `merge(intervals)` that merges overlapping intervals and refuses
anything that is not a list of pairs.
