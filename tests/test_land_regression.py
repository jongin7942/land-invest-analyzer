"""기존 토지 급매 탐지기 회귀 테스트.

각 PHASE 를 끝낼 때마다 이걸 돌린다. "기존에 잘 작동하는 기능은 최대한 유지"가
지켜지는지 확인하는 게 목적이라, 여기서는 아파트 엔진 코드를 전혀 쓰지 않는다.

네트워크를 타지 않는 순수 함수와 import 만 검사한다 — API 키 없이도 돌아가야
CI 든 로컬이든 매번 실행할 수 있다.
"""
import pytest

import config


class TestModulesStillImport:
    def test_수집기(self):
        from collectors import land_trade, land_characteristics, news, onbid  # noqa: F401

    def test_분석_모듈(self):
        from analysis import (  # noqa: F401
            category, due_diligence, narrative, price_baseline, road_access, zoning_rules,
        )

    def test_진입점(self):
        import analyze, build_static, main, pipeline  # noqa: F401
        from db import schema  # noqa: F401
        from notify import alert, kakao  # noqa: F401


class TestFlaskAppIntact:
    def test_라우트가_그대로다(self):
        from app import app
        rules = {r.rule for r in app.url_map.iter_rules()}
        assert "/" in rules
        assert "/candidate/<int:cid>" in rules

    def test_정적사이트_빌더가_같은_템플릿을_쓴다(self):
        # build_static.py 가 app.jinja_env 를 재사용하는 구조를 계속 유지하는지.
        from app import app
        for name in ("base.html", "list.html", "detail.html", "static_index.html"):
            assert app.jinja_env.get_template(name) is not None


class TestPureCalculations:
    """숫자가 바뀌면 저장된 기존 후보 점수와 어긋난다."""

    def test_도로접면_판정(self):
        from analysis import road_access as ra
        assert ra.classify("맹지")["grade"] == ra.MENGJI
        assert ra.classify("세로(불)")["grade"] == ra.BLOCKED
        assert ra.classify("세로(가)")["grade"] == ra.NARROW
        assert ra.classify("광대한면")["grade"] == ra.OK
        assert ra.classify(None)["grade"] == ra.UNKNOWN
        assert ra.classify("맹지")["buildable"] is False

    def test_용도지역_건축규모(self):
        from analysis import zoning_rules as zr
        bd = zr.buildable("계획관리지역", 1000.0)
        assert (bd["bcr"], bd["far"]) == (40, 100)
        assert bd["footprint_m2"] == 400.0
        assert bd["gross_m2"] == 1000.0
        assert zr.buildable("없는용도지역", 1000.0) is None
        assert zr.limits_for("농림지역")[2] is True  # 신축제한 플래그

    def test_평당가(self):
        import pytest
        from analysis import price_baseline as pb
        # 1000㎡ ≒ 302.5평, 30,250만원 → 평당 약 100만원
        assert pb.price_per_pyeong(30_250, 1000.0) == pytest.approx(100.0, abs=0.01)
        assert pb.price_per_pyeong(None, 1000.0) is None
        assert pb.price_per_pyeong(30_250, 0) is None

    def test_면적_구간(self):
        from analysis import price_baseline as pb
        assert pb.size_bucket(100) == "소형(~200평)"
        assert pb.size_bucket(500) == "중형(200~1000평)"
        assert pb.size_bucket(7021) == "대형(1000평~)"

    def test_지목_분류와_보상배율(self):
        from analysis import category as cat
        assert cat.land_group("임야") == "농지군"
        assert cat.land_group("대") == "대지군"
        assert cat.land_group("없는지목") == "미분류"
        assert cat.COMPENSATION_MULTIPLIER["농지군"] == 1.1

    def test_공유지분_판정(self):
        from analysis import due_diligence as dd
        assert dd.is_co_ownership("○○리 100-1 지분") is True
        assert dd.is_co_ownership("○○리 100-1") is False
        assert dd.is_co_ownership(None) is False

    def test_주소_파싱(self):
        from pipeline import parse_sgg_umd
        assert parse_sgg_umd("경기도 안성시 양성면 노곡리 1") == ("안성시", "양성면 노곡리")
        assert parse_sgg_umd("경기도 성남시 분당구 정자동 1") == ("성남시 분당구", "정자동")
        assert parse_sgg_umd(None) == (None, None)


class TestCliHelp:
    """`--help` 조차 안 뜨면 그 CLI 는 사실상 고장난 것이다.

    실제로 analyze.py 는 help 문자열의 `%` 를 argparse 가 %-포맷하려다 죽어서
    인자 없이 실행하면 ValueError 가 났다(PHASE 0에서 `%%` 로 수정). 같은 실수가
    다시 들어오지 않게 네 진입점의 도움말 렌더링을 전부 검사한다.
    """

    @pytest.mark.parametrize("module_name", ["main", "analyze", "pipeline", "notify.alert"])
    def test_도움말이_렌더링된다(self, module_name, capsys):
        import importlib
        module = importlib.import_module(module_name)
        with pytest.raises(SystemExit) as exc:
            module.main(["--help"])
        assert exc.value.code == 0
        assert "usage:" in capsys.readouterr().out


class TestSchemaUntouched:
    def test_토지_테이블이_그대로_있다(self):
        from db.schema import SCHEMA
        assert "CREATE TABLE IF NOT EXISTS land_trade" in SCHEMA
        assert "CREATE TABLE IF NOT EXISTS auction_candidate" in SCHEMA


class TestDatabasesAreSeparate:
    def test_토지_DB와_아파트_DB는_다른_파일(self):
        # 아파트 마이그레이션이 land_invest.db 를 절대 건드리지 못하게 하는 전제.
        assert config.DB_PATH != config.APT_DB_PATH
        assert config.DB_PATH.endswith("land_invest.db")
        assert config.APT_DB_PATH.endswith("apt_invest.db")

    def test_아파트_엔진은_토지_모듈을_import_하지_않는다(self):
        import ast
        from pathlib import Path

        forbidden = {"analysis", "collectors", "db", "pipeline", "app",
                     "main", "analyze", "notify", "build_static", "publish"}
        offenders = []
        for path in Path(__file__).resolve().parents[1].joinpath("apt_engine").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    if name.split(".")[0] in forbidden:
                        offenders.append(f"{path.name}: {name}")
        assert offenders == [], f"아파트 엔진이 토지 모듈을 참조합니다: {offenders}"

    def test_모듈_최상위에_같은_이름의_함수가_두_번_정의되지_않는다(self):
        """뒤에 정의된 함수가 앞의 것을 조용히 덮어쓰는 사고를 막는다.

        실제로 `_resolve_complex` 를 두 번 정의해 cash·loan·relative·catalyst 가
        통째로 깨진 적이 있다. import 는 성공하고 --help 도 통과해서, 그 명령을
        실행해 보기 전까지 아무도 모른다.
        """
        import ast
        from collections import Counter
        from pathlib import Path

        offenders = []
        root = Path(__file__).resolve().parents[1]
        for path in list(root.joinpath("apt_engine").rglob("*.py")) + [
                root / "pipeline.py", root / "app.py"]:
            if not path.exists():
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = Counter(n.name for n in tree.body
                            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
            for name, count in names.items():
                if count > 1:
                    offenders.append(f"{path.relative_to(root)}: {name} × {count}")
        assert offenders == [], f"같은 이름의 함수가 여러 번 정의됐습니다: {offenders}"
