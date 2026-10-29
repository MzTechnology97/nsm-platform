# NSM external probe: build from the repository root
#   docker build -f probe/Dockerfile -t nsm-probe .
FROM python:3.12-slim
RUN useradd --system --no-create-home probe
WORKDIR /probe
COPY app/app/port_probes.py probe/nsm_probe.py ./
USER probe
EXPOSE 8788
CMD ["python", "-u", "nsm_probe.py"]
