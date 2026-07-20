import pytest
import music as M


@pytest.fixture(autouse=True)
def musica_on():
    M.MUSICA_ENABLED = True
    yield


CASOS = [
    ("pon despacito",                          "play",     "despacito"),
    ("reproduce bohemian rhapsody",            "play",     "bohemian rhapsody"),
    ("ponme la canción shape of you",          "play",     "shape of you"),
    ("quiero escuchar a soda stereo",          "play",     "soda stereo"),
    ("toca algo de los beatles",               "play",     "algo de los beatles"),
    ("pon mi playlist de rock",                "play_playlist", "rock"),
    ("reproduce mis playlists",                "play_playlist", None),
    ("pon la playlist favoritas",              "play_playlist", "favoritas"),
    ("pon mi lista de entrenamiento",          "play_playlist", "entrenamiento"),
    ("pon música",                             "resume",   None),
    ("dale play",                              "resume",   None),
    ("pausa la música",                        "pause",    None),
    ("pará la música",                         "pause",    None),
    ("siguiente canción",                      "next",     None),
    ("cambia de canción",                      "next",     None),
    ("canción anterior",                       "prev",     None),
    ("sube el volumen",                        "vol_up",   None),
    ("más fuerte",                             "vol_up",   None),
    ("baja el volumen",                        "vol_down", None),
    ("pon el volumen al 70 por ciento",        "vol_set",  None),
    ("dame un poco de volumen al 30",          "vol_set",  None),
]


def test_parse_music_command():
    for texto, acc, q in CASOS:
        intent = M.parse_music_command(texto)
        assert intent is not None, f"'{texto}' devolvió None"
        assert intent.accion == acc, f"'{texto}': accion {intent.accion} != {acc}"
        assert intent.query == q, f"'{texto}': query {intent.query!r} != {q!r}"


NEGATIVOS = [
    "hola Bob, ¿cómo estás?",
    "cuéntame un chiste",
    "¿qué hora es?",
    "recuérdame en 5 minutos algo",
    "tengo una playlist de rock",
]


def test_no_false_positives():
    for texto in NEGATIVOS:
        assert M.parse_music_command(texto) is None, f"'{texto}' debió devolver None"


def test_disabled_returns_none():
    M.MUSICA_ENABLED = False
    assert M.parse_music_command("pon despacito") is None
    M.MUSICA_ENABLED = True


def test_vol_set_value():
    intent = M.parse_music_command("pon el volumen al 50 por ciento")
    assert intent is not None
    assert intent.accion == "vol_set"
    assert intent.valor == 50


def test_vol_set_word():
    intent = M.parse_music_command("pon el volumen a treinta")
    assert intent is not None
    assert intent.accion == "vol_set"
    assert intent.valor == 30
