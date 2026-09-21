from examples.demo_agent import QUESTIONS, answer


def test_every_question_is_answered():
    """Every question the agent claims to handle is asked here, so every one of them is traced.

    The eval suite is built from traced runs, so a question that is never asked can never become
    a regression case.
    """
    for q in QUESTIONS:
        out = answer(q)
        assert out and out != "I do not know.", q


def test_unknown_question_says_so():
    assert answer("What colour is it?") == "I do not know."
