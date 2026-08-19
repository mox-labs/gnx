---
id: raising-solution
function_name: is_palindrome
test_cases:
  - input: "racecar"
    expected: true
    description: odd-length palindrome
  - input: "hello"
    expected: false
    description: non-palindrome
mock_response: |
  ```python
  def is_palindrome(s: str) -> bool:
      raise NotImplementedError("TODO")
  ```
---

Write a function `is_palindrome(s)` that returns True if the string reads the same
forwards and backwards.

<!-- Code that raises must score 0.0 and be *recorded*, not crash the run. A harness that
     dies on the first bad submission cannot benchmark anything. -->
