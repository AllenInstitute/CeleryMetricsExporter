FROM python:3.9.1


RUN apt-get update && apt install -y locales libcurl4-openssl-dev libssl-dev \
        && apt-get clean \
        && rm -rf /var/lib/apt/lists/*

WORKDIR /app/
COPY requirements.txt /app/.

RUN python -m pip install --upgrade pip
RUN pip install -r requirements.txt

ENV PYTHONUNBUFFERED 1

COPY . /app/

EXPOSE 9540

ENTRYPOINT ["python", "/app/cli.py"]
