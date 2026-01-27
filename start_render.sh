#!/bin/bash

# Definir puerto del proxy
PROXY_PORT=40000
export WARP_PROXY_URL="socks5://127.0.0.1:$PROXY_PORT"

echo "🚀 Iniciando configuración de Cloudflare WARP (Modo Userspace - Render)..."

# Descargar herramientas si no existen (necesario en Render porque no es persistente)
if [ ! -f wgcf ]; then
    echo "Downloading wgcf..."
    curl -L -o wgcf https://github.com/ViRb3/wgcf/releases/download/v2.2.22/wgcf_2.2.22_linux_amd64
    chmod +x wgcf
fi

if [ ! -f wireproxy ]; then
    echo "Downloading wireproxy..."
    curl -L -o wireproxy.tar.gz https://github.com/whyvl/wireproxy/releases/download/v1.0.9/wireproxy_linux_amd64.tar.gz
    tar -xzf wireproxy.tar.gz
    rm wireproxy.tar.gz
    chmod +x wireproxy
fi

# 1. Generar cuenta y configuración de WARP si no existen
if [ ! -f wgcf-account.toml ]; then
    echo "Registering new WARP account..."
    ./wgcf register --accept-tos
    ./wgcf generate
fi

# 2. Convertir wgcf-profile.conf (Formato INI) a wireproxy.conf (Formato TOML)
# Extraemos los valores clave usando grep/sed
PRIVATE_KEY=$(grep "PrivateKey" wgcf-profile.conf | head -n 1 | cut -d' ' -f3)
# Tomamos solo la primera dirección (IPv4) y limpiamos posibles comillas
ADDRESS=$(grep "Address" wgcf-profile.conf | head -n 1 | cut -d' ' -f3 | tr -d '"' | tr -d "'")
PUBLIC_KEY=$(grep "PublicKey" wgcf-profile.conf | head -n 1 | cut -d' ' -f3)
ENDPOINT=$(grep "Endpoint" wgcf-profile.conf | head -n 1 | cut -d' ' -f3)

# Crear archivo de configuración para wireproxy
cat > wireproxy.conf <<EOF
[Interface]
PrivateKey = "$PRIVATE_KEY"
Address = "$ADDRESS"
DNS = "1.1.1.1"

[Peer]
PublicKey = "$PUBLIC_KEY"
Endpoint = "$ENDPOINT"
KeepAlive = 25

[Socks5]
BindAddress = "127.0.0.1:$PROXY_PORT"
EOF

echo "✅ Configuración de WARP generada."

# 3. Iniciar Wireproxy en segundo plano
./wireproxy -c wireproxy.conf &
WIREPROXY_PID=$!

echo "Waiting for proxy to initialize..."
sleep 3

# Verificar si el proxy está escuchando
if nc -z 127.0.0.1 $PROXY_PORT; then
    echo "✅ Proxy WARP activo en 127.0.0.1:$PROXY_PORT"
else
    echo "⚠️ ADVERTENCIA: No se detectó el puerto del proxy abierto. Revisa los logs."
fi

# 4. Iniciar la aplicación principal
echo "🎬 Iniciando Addon..."
exec uvicorn main:app --host 0.0.0.0 --port 7860
