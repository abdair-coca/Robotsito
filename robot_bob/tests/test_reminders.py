import pytest
from reminders import parse_recordatorio, Recordatorio
from datetime import datetime, timedelta

AHORA = datetime(2026, 7, 20, 14, 30, 0)
AHORA_TS = AHORA.timestamp()


def test_rel_segundos():
    r = parse_recordatorio("recuérdame en 30 seg comprar leche", AHORA)
    assert r is not None, "debió parsear"
    assert abs(r.due_ts - (AHORA_TS + 30)) < 1
    assert "leche" in r.que.lower()


def test_rel_minutos():
    r = parse_recordatorio("avísame en 5 min llamar a Juan", AHORA)
    assert r is not None
    assert abs(r.due_ts - (AHORA_TS + 300)) < 1


def test_rel_horas():
    r = parse_recordatorio("recuérdame en 2 horas la reunión", AHORA)
    assert r is not None
    assert abs(r.due_ts - (AHORA_TS + 7200)) < 1


def test_rel_numero_palabra():
    r = parse_recordatorio("recordatorio en quince min pastel", AHORA)
    assert r is not None
    assert abs(r.due_ts - (AHORA_TS + 900)) < 1


def test_rel_media_hora():
    r = parse_recordatorio("recuérdame en media hora comer", AHORA)
    assert r is not None
    assert abs(r.due_ts - (AHORA_TS + 1800)) < 1


def test_rel_hora_y_media():
    r = parse_recordatorio("avísame en 1 hora y media doctor", AHORA)
    assert r is not None
    assert abs(r.due_ts - (AHORA_TS + 5400)) < 1


def test_abs_a_las():
    r = parse_recordatorio("recuérdame a las 16:00 tomar agua", AHORA)
    assert r is not None
    # 16:00 hoy = 16:00 - 14:30 = 1.5h = 5400s
    assert abs(r.due_ts - (AHORA_TS + 5400)) < 1


def test_abs_a_la_una():
    r = parse_recordatorio("recordatorio a la una comer", AHORA)
    assert r is not None
    # 1:00 = 13:00, but since AHORA is 14:30, it should be tomorrow
    # Actually a la una = 13:00, which is earlier than 14:30, so should be tomorrow
    assert r.due_ts > AHORA_TS + 3600  # at least 1h ahead


def test_charla_normal_no_parsea():
    assert parse_recordatorio("hola Bob cómo estás", AHORA) is None
    assert parse_recordatorio("cuéntame un chiste", AHORA) is None
    assert parse_recordatorio("qué hora es", AHORA) is None


def test_gatillo_sin_tiempo():
    assert parse_recordatorio("recuérdame algo", AHORA) is None


def test_despiertame():
    r = parse_recordatorio("despiértame en 10 min siesta", AHORA)
    assert r is not None
    assert abs(r.due_ts - (AHORA_TS + 600)) < 1


def test_alarma():
    r = parse_recordatorio("pon una alarma a las 7:00", AHORA)
    assert r is not None
