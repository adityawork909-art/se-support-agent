from examples.demo_agent import answer


def test_voltage_is_answered_in_volts():
    assert "400 volts" in answer("What is the voltage?")


def test_breaker_rating_is_answered():
    assert "63 amps" in answer("When does the breaker trip?")
