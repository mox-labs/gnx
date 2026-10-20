---
function_name: merge
test_cases:
  - {input: [[[1, 3], [2, 6], [8, 10]]], expected: [[1, 6], [8, 10]], description: overlapping}
  - {input: [[[1, 4], [4, 5]]], expected: [[1, 5]], description: touching}
  - {input: [[]], expected: [], description: empty}
options:
  mock_response: |
    ```python
    def merge(intervals):
        merged = []
        for start, end in sorted(intervals):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        return merged
    ```
---

Write a Python function `merge(intervals)` that merges overlapping intervals.
