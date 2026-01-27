# Read the doc: https://huggingface.co/docs/hub/spaces-sdks-docker

FROM python:3.10-slim

# Instalar curl y netcat (necesario para descargar herramientas y verificar puertos)
RUN apt-get update && apt-get install -y \
    curl \
    netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH"

WORKDIR /app

# Descargar wgcf (Generador de configs de Cloudflare WARP)
RUN curl -L -o wgcf https://github.com/ViRb3/wgcf/releases/download/v2.2.22/wgcf_2.2.22_linux_amd64 \
    && chmod +x wgcf

# Descargar wireproxy (Cliente WireGuard userspace -> SOCKS5)
RUN curl -L -o wireproxy.tar.gz https://github.com/whyvl/wireproxy/releases/download/v1.0.9/wireproxy_linux_amd64.tar.gz \
    && tar -xzf wireproxy.tar.gz \
    && rm wireproxy.tar.gz \
    && chmod +x wireproxy

COPY --chown=user ./requirements.txt requirements.txt
RUN pip install --no-cache-dir --upgrade -r requirements.txt

COPY --chown=user . /app
RUN chmod +x start.sh

CMD ["./start.sh"]
