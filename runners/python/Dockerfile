FROM python:3.12-slim

RUN useradd \
    --create-home \
    --shell /usr/sbin/nologin \
    runner

RUN mkdir /workspace /runner \
    && chown runner:runner /workspace

COPY run.sh /runner/run.sh
RUN chmod 0555 /runner/run.sh

ENV PYTHONUNBUFFERED=1

USER runner
WORKDIR /workspace

CMD ["/runner/run.sh"]