# 클라우드에 올려서 **PC 를 꺼도 살아 있는 링크**를 만들 때 쓴다.
#
# DB 는 이미지에 굽지 않는다. 수집이 끝나면 수 GB 가 되는데 이미지에 넣으면
# 코드 한 줄 고칠 때마다 그걸 다시 올려야 한다. 볼륨에 올려 두고 마운트한다.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APT_PUBLIC=1 \
    APT_DB_PATH=/data/apt_invest.db \
    PORT=8080

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY apt_engine/ ./apt_engine/
COPY templates/ ./templates/
COPY static/ ./static/
COPY rules/ ./rules/
COPY apt_app.py serve.py config.py ./

EXPOSE 8080
# 읽기 전용 DB 라서 프로세스를 늘릴 이유가 없다. 스레드로 충분하다.
CMD ["python", "serve.py"]
