import pytest
from datetime import datetime
from assistant import _quiere_hora, _quiere_fecha, _quiere_clima, _quiere_noticias
from assistant import hora_actual, fecha_actual


class TestIntencion:
    def test_hora_positivo(self):
        assert _quiere_hora("qué hora es")
        assert _quiere_hora("decime la hora")

    def test_hora_negativo(self):
        assert not _quiere_hora("cuéntame un chiste")
        assert not _quiere_hora("cómo está el clima")

    def test_fecha_positivo(self):
        assert _quiere_fecha("qué día es hoy")
        assert _quiere_fecha("qué fecha es")

    def test_fecha_negativo(self):
        assert not _quiere_fecha("reproduce música")

    def test_clima_positivo(self):
        assert _quiere_clima("qué temperatura hace")
        assert _quiere_clima("cómo está el clima")

    def test_clima_negativo(self):
        assert not _quiere_clima("qué hora es")

    def test_noticias_positivo(self):
        assert _quiere_noticias("dame las noticias")
        assert _quiere_noticias("qué pasa en el mundo")

    def test_noticias_negativo(self):
        assert not _quiere_noticias("pon música")


class TestHoraFecha:
    def test_hora_formato(self):
        h = hora_actual(datetime(2026, 7, 20, 14, 30, 0))
        assert h == "14:30"

    def test_hora_medianoche(self):
        h = hora_actual(datetime(2026, 1, 1, 0, 5, 0))
        assert h == "00:05"

    def test_fecha_formato(self):
        f = fecha_actual(datetime(2026, 7, 20, 14, 30, 0))
        assert "lunes" in f
        assert "20" in f
        assert "julio" in f
        assert "2026" in f

    def test_fecha_enero(self):
        f = fecha_actual(datetime(2026, 1, 1, 12, 0, 0))
        assert "jueves" in f
        assert "1" in f
        assert "enero" in f
