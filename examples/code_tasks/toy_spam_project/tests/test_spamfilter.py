from __future__ import annotations

import unittest

from spamfilter import classify, score_message


class SpamFilterTests(unittest.TestCase):
    def test_existing_keyword_spam(self) -> None:
        self.assertEqual(classify("You are a winner"), "spam")

    def test_ordinary_message_is_ham(self) -> None:
        self.assertEqual(classify("Can we move the meeting to Tuesday?"), "ham")

    def test_lottery_prize_messages_are_spam(self) -> None:
        self.assertEqual(classify("urgent lottery prize waiting"), "spam")

    def test_score_is_normalized(self) -> None:
        self.assertLessEqual(score_message("free win winner"), 1.0)


if __name__ == "__main__":
    unittest.main()
