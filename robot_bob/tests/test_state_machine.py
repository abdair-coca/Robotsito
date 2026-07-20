import time
import pytest
from state_machine import StateMachine, RobotState


@pytest.fixture
def sm(dummy_serial):
    return StateMachine(dummy_serial)


def _avanzar(sm, seg):
    sm._t_ei_tick -= seg
    sm.tick_estados_internos()


class TestEnergia:
    def test_baja_hablando(self, sm):
        sm.iniciar_hablando()
        e0 = sm.energia
        _avanzar(sm, 5.0)
        assert sm.energia < e0

    def test_recupera_dormido(self, sm):
        sm._transicionar(RobotState.IDLE)
        sm._t_sin_presencia = time.monotonic() - 60.0
        sm.energia = 0.3
        e0 = sm.energia
        _avanzar(sm, 5.0)
        assert sm.is_asleep()
        assert sm.energia > e0


class TestEventoCharla:
    def test_charla_buena(self, sm):
        sm.sociabilidad = sm.motivacion = sm.curiosidad = 0.5
        sm.energia = 0.8
        sm.ei_evento_charla(turnos=5, mood=0.6)
        assert sm.sociabilidad > 0.5
        assert sm.motivacion > 0.5
        assert sm.curiosidad < 0.5
        assert sm.energia < 0.8


class TestFactorIniciativa:
    def test_rango(self, sm):
        sm.motivacion = sm.sociabilidad = sm.energia = 0.0
        f_bajo = sm.factor_iniciativa()
        sm.motivacion = sm.sociabilidad = sm.energia = 1.0
        f_alto = sm.factor_iniciativa()
        assert f_bajo < f_alto
        assert f_bajo >= 0.2

    def test_para_arriba(self, sm):
        sm.motivacion = sm.sociabilidad = sm.energia = 0.0
        assert sm.factor_iniciativa() < 0.5


class TestEstadoInternoPrompt:
    def test_neutro(self, sm):
        sm.energia = sm.motivacion = sm.curiosidad = sm.sociabilidad = 0.5
        assert sm.estado_interno_prompt() == ''

    def test_extremos(self, sm):
        sm.energia, sm.curiosidad = 0.1, 0.9
        p = sm.estado_interno_prompt()
        assert 'cansado' in p
        assert 'curioso' in p


class TestPresencia:
    def test_cara_nueva_sube_curiosidad(self, sm):
        sm._transicionar(RobotState.IDLE)
        sm.curiosidad = 0.4
        c0 = sm.curiosidad
        sm.notificar_cara(True)
        assert sm.curiosidad > c0
        assert sm.estado == RobotState.PRESENCE
