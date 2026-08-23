from settings import PetSettings
from state_machine import PetAction, PetStateMachine


def test_random_action_never_repeats_current(monkeypatch):
    state = PetStateMachine(PetSettings())
    monkeypatch.setattr("state_machine.random.choice", lambda choices: choices[0])
    state.random_action()
    assert state.target_action != PetAction.IDLE
    assert state.action_duration == 12.0


def test_action_layers_crossfade_without_brightness_jump():
    state = PetStateMachine(PetSettings())
    state.set_action(PetAction.PLAY)
    state.transition_progress = 0.5
    layers = state.action_layers()
    assert layers == [(PetAction.IDLE, 0.5), (PetAction.PLAY, 0.5)]
    assert sum(alpha for _, alpha in layers) == 1.0


def test_play_drag_faces_pointer_direction():
    state = PetStateMachine(PetSettings())
    state.start_drag()
    state.update_drag(30, 0)
    pose = state.pose_for(PetAction.PLAY)
    assert 89.0 <= pose.rotation <= 91.0


def test_accent_blends_during_transition():
    state = PetStateMachine(PetSettings())
    state.set_action(PetAction.CHEER)
    state.transition_progress = 0.5
    assert state.current_accent() == "#7ACECF"
