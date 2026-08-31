"""cli 가 부르는 이름이 실제로 존재하는지 — 모듈 간 계약 테스트.

이 파일이 생긴 이유:

`collect_complexes` 를 리팩터링하다가 바로 아래 있던 `_collect_deals` ·
`collect_trades` · `collect_rents` 를 통째로 지웠는데, **테스트 830개가 전부
통과했다.** 그 함수들을 부르는 테스트가 하나도 없었기 때문이다. 깨진 건
`collect trades` 를 실제로 돌린 뒤에야 드러났고, 그때는 이미 커밋·푸시된 뒤였다.

느린 수집을 테스트에서 실행할 수는 없다. 대신 **cli.py 가 참조하는 이름이
그 모듈에 있는지**만 정적으로 확인한다. 호출 한 번 없이도 '지워졌다'는 사실은 잡힌다.
"""
import ast
import importlib
import pathlib

import pytest

CLI_PATH = pathlib.Path(__file__).resolve().parents[1] / "apt_engine" / "cli.py"

# cli 안에서 `<별칭>.<이름>(...)` 으로 불리는 모듈들.
# 별칭 → 실제 모듈 경로. cli 가 함수 안에서 지연 import 하는 것도 포함한다.
WATCHED = {
    "ingest": "apt_engine.ingest",
    "regions": "apt_engine.regions",
    "repo": "apt_engine.repo.apt",
    "rule_repo": "apt_engine.repo.rules",
}


def _called_attrs(alias: str) -> set[str]:
    """cli.py 에서 `alias.foo(` 로 호출되는 foo 들."""
    tree = ast.parse(CLI_PATH.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == alias):
            found.add(node.func.attr)
    return found


@pytest.mark.parametrize("alias,module_path", sorted(WATCHED.items()))
def test_cli_가_부르는_이름이_모듈에_있다(alias, module_path):
    module = importlib.import_module(module_path)
    called = _called_attrs(alias)
    missing = sorted(n for n in called if not hasattr(module, n))
    assert not missing, (
        f"cli.py 가 {alias}.{{...}} 로 부르는데 {module_path} 에 없는 이름: {missing}. "
        f"리팩터링하다 지웠을 가능성이 큽니다.")


def test_수집_진입점은_반드시_존재한다():
    """가장 비싼 경로라 실수로 지워지면 몇 시간을 날린다. 이름을 못박아 둔다."""
    ingest = importlib.import_module("apt_engine.ingest")
    for name in ("collect_complexes", "collect_trades", "collect_rents",
                 "run_matching", "build_snapshots"):
        assert callable(getattr(ingest, name, None)), f"ingest.{name} 이 없습니다"


def test_모든_서브커맨드_핸들러가_연결돼_있다():
    """`cli --help` 에 나오는 명령이 전부 실제 핸들러를 가리키는지."""
    cli = importlib.import_module("apt_engine.cli")
    parser = cli.build_parser()
    actions = [a for a in parser._actions if getattr(a, "choices", None)
               and hasattr(a, "dest") and a.dest == "cmd"]
    assert actions, "서브커맨드 파서를 찾지 못했습니다"
    for name, sub in actions[0].choices.items():
        handler = sub.get_default("func") if hasattr(sub, "get_default") else None
        # 핸들러를 set_defaults 로 붙이지 않는 구조면 이름 규칙으로 확인한다.
        if handler is None:
            handler = getattr(cli, f"cmd_{name.replace('-', '_')}", None)
        assert handler is not None and callable(handler), \
            f"'{name}' 명령의 핸들러가 없습니다"
