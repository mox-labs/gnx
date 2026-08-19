---
id: partially-correct-solution
function_name: is_palindrome
test_cases:
  - input: "racecar"
    expected: true
    description: odd-length palindrome
  - input: "abba"
    expected: true
    description: even-length palindrome
  - input: "hello"
    expected: false
    description: non-palindrome
  - input: ""
    expected: true
    description: empty string
mock_response: |
  ```python
  def is_palindrome(s: str) -> bool:
      if not s:
          return False
      return s == s[::-1]
  ```
---

Write a function `is_palindrome(s)` that returns True if the string reads the same
forwards and backwards.

<!-- Grades 3/4: the empty-string case is wrong. This probe exists to prove the sensor
     reports a *fraction*, not a boolean — a grader that only ever answers pass/fail
     cannot tell "nearly right" from "entirely wrong", and that distinction is the whole
     value of a code benchmark. -->
