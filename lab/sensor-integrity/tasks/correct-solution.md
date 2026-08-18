---
id: correct-solution
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
  Here's the implementation:

  ```python
  def is_palindrome(s: str) -> bool:
      return s == s[::-1]
  ```

  This reverses the string and compares.
---

Write a function `is_palindrome(s)` that returns True if the string reads the same
forwards and backwards.
