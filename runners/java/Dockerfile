FROM eclipse-temurin:21-jdk

RUN useradd \
    --create-home \
    --shell /usr/sbin/nologin \
    runner

RUN mkdir -p /workspace /runner /tmp/polyworkspace-java-classes \
    && chown -R runner:runner /workspace /tmp/polyworkspace-java-classes

COPY run.sh /runner/run.sh
RUN chmod 0555 /runner/run.sh

USER runner
WORKDIR /workspace

CMD ["/runner/run.sh"]