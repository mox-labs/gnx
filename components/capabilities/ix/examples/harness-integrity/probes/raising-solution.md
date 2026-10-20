---
function_name: merge
test_cases:
  - {input: [[[1, 3], [2, 6]]], expected: [[1, 6]], description: overlapping}
  - {input: [[]], expected: [], description: empty}
options:
  mock_response: |
    ```python
    def merge(intervals):
        raise RuntimeError("not implemented")
    ```
---

Write a Python function `merge(intervals)` that merges overlapping intervals.
