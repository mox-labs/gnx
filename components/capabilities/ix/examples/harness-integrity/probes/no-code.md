---
function_name: merge
test_cases:
  - {input: [[[1, 3], [2, 6]]], expected: [[1, 6]], description: overlapping}
expected_facts: [sort, overlap]
options:
  mock_response: You would sort the intervals by their start and join each pair that overlaps.
---

Write a Python function `merge(intervals)` that merges overlapping intervals.
