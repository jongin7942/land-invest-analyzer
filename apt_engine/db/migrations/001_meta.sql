-- 001 · 공통 메타 계층 (PHASE 0)
--
-- 도메인 테이블(complex, trade, price_snapshot …)은 PHASE 1부터 붙는다.
-- 여기 있는 세 테이블은 그 전에 있어야 하는 것들이다 — 요구사항 25(모든 데이터에
-- source / retrieved_at / effective_date / confidence)와 E-10(수집 실패와 데이터
-- 없음을 구분)이 스키마 레벨에서 성립하려면, 출처와 수집이력이 먼저 존재해야 한다.

-- ── 출처 ──────────────────────────────────────────────────────────────
-- 모든 원자료 테이블이 source_id 로 이 표를 가리킨다. "이 숫자 어디서 왔나"의 뿌리.
CREATE TABLE data_source (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    key          TEXT NOT NULL UNIQUE,   -- 코드에서 참조하는 안정적 식별자 (molit_apt_trade 등)
    name         TEXT NOT NULL,          -- 사람이 읽는 이름
    kind         TEXT NOT NULL
                 CHECK (kind IN ('API', '공식문서', '수기', '추정', '시나리오')),
    url          TEXT,
    license      TEXT,
    note         TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ── 수집 이력 ─────────────────────────────────────────────────────────
-- 기존 파이프라인은 예외를 광범위하게 삼켜서(`except Exception` 후 진행,
-- 뉴스 실패 시 빈 리스트 반환) "데이터가 원래 없음"과 "수집이 실패함"이
-- 구분되지 않는다. 신뢰도를 표시하려면 실패를 데이터로 남겨야 한다.
CREATE TABLE collection_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key   TEXT NOT NULL,          -- data_source.key (수집 시점에 출처가 미등록일 수 있어 FK 미설정)
    target       TEXT,                   -- 시군구코드·단지코드 등 수집 대상
    period       TEXT,                   -- YYYYMM 등 대상 기간
    status       TEXT NOT NULL
                 CHECK (status IN ('OK', 'EMPTY', 'FAILED')),
    row_count    INTEGER,
    error        TEXT,                   -- FAILED 일 때 사유 원문
    ran_at       TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX idx_collection_log_lookup ON collection_log (source_key, target, period);
CREATE INDEX idx_collection_log_status ON collection_log (status, ran_at);

-- ── 계산엔진 버전 ─────────────────────────────────────────────────────
-- 파생 레코드는 engine_version 을 함께 저장한다. 산식이 바뀌면 여기에 한 줄
-- 남기고, 옛 버전으로 계산된 파생값을 재계산 대상으로 골라낼 수 있게 한다.
CREATE TABLE engine_version (
    version      TEXT PRIMARY KEY,
    changed_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    note         TEXT
);

INSERT INTO engine_version (version, note) VALUES ('0.1.0', 'PHASE 0 — 기반(단위계·Calc 추적·마이그레이션)');
