import pytest
from expression_engine import (
    is_happy, is_love, is_goodbye, is_confused, is_insult,
    is_laugh, is_concern, is_absurd_cute, is_deep_question,
    mood_delta_for_user_text,
)


class TestIsHappy:
    def test_positivo(self):
        assert is_happy("qué genial")
        assert is_happy("me siento feliz")
        assert is_happy("excelente")

    def test_negativo(self):
        assert not is_happy("odio esto")
        assert not is_happy("qué triste")


class TestIsLove:
    def test_positivo(self):
        assert is_love("te quiero")
        assert is_love("te amo mucho")
        assert is_love("eres genial")

    def test_negativo(self):
        assert not is_love("te odio")
        assert not is_love("no me gustas")


class TestIsGoodbye:
    def test_positivo(self):
        assert is_goodbye("adiós")
        assert is_goodbye("nos vemos")
        assert is_goodbye("hasta luego")

    def test_negativo(self):
        assert not is_goodbye("hola")
        assert not is_goodbye("cómo estás")


class TestIsConfused:
    def test_positivo(self):
        assert is_confused("no entiendo")
        assert is_confused("qué significa eso")

    def test_negativo(self):
        assert not is_confused("hola")


class TestIsInsult:
    def test_positivo(self):
        assert is_insult("eres tonto")
        assert is_insult("qué idiota eres")

    def test_negativo(self):
        assert not is_insult("qué bonito")


class TestIsLaugh:
    def test_positivo(self):
        assert is_laugh("jajaja")
        assert is_laugh("qué chistoso")

    def test_negativo(self):
        assert not is_laugh("qué triste")


class TestIsConcern:
    def test_positivo(self):
        assert is_concern("estás bien")
        assert is_concern("qué te pasa")

    def test_negativo(self):
        assert not is_concern("hola")


class TestIsAbsurdCute:
    def test_positivo(self):
        assert is_absurd_cute("tienes hambre")
        assert is_absurd_cute("cuántos años tienes")
        assert is_absurd_cute("tienes novia")

    def test_negativo(self):
        assert not is_absurd_cute("hola")


class TestIsDeepQuestion:
    def test_positivo(self):
        assert is_deep_question("cuál es el sentido de la vida")
        assert is_deep_question("qué es la felicidad")
        assert is_deep_question("existe dios")

    def test_negativo(self):
        assert not is_deep_question("qué hora es")


class TestMoodDelta:
    def test_amor(self):
        assert mood_delta_for_user_text("te quiero") > 0

    def test_insulto(self):
        assert mood_delta_for_user_text("eres tonto") < 0

    def test_neutral(self):
        assert mood_delta_for_user_text("hola") == 0.05
