import socket

import pytest

from conftest import fixture_text, load_json


def test_network_is_disabled_by_default():
    with pytest.raises(RuntimeError, match="network disabled"):
        socket.create_connection(("127.0.0.1", 11434), timeout=1)


def test_fixtures_contain_panel_cases():
    names = {f["name"] for f in load_json("outdated.json")["formulae"]}
    assert {
        "vectordotdev/brew/vector",
        "aws-c-auth",
        "aws-c-s3",
        "awscli",
        "redis",
        "rabbitmq",
        "jandedobbeleer/oh-my-posh/oh-my-posh",
    } <= names
    info = {f["full_name"] for f in load_json("info.json")["formulae"]}
    assert "vectordotdev/brew/vector" in info
    assert "Calling `postflight` is deprecated" in fixture_text("ngrok_stderr.txt")
