#!/bin/sh
# Install Debian/Ubuntu packages during an image build.
#   dd-system-setup <package>...
# With the optional build secret "ca" (a CA bundle for a TLS-intercepting egress proxy), apt
# is switched to HTTPS mirrors and told to trust that CA for the build only; nothing of it is
# left in the image.
set -eu
if [ -f /run/secrets/ca ]; then
  cp /run/secrets/ca /tmp/dd-build-ca.crt
  chmod 644 /tmp/dd-build-ca.crt  # apt fetches as the unprivileged _apt user
  echo 'Acquire::https::CAInfo "/tmp/dd-build-ca.crt";' > /etc/apt/apt.conf.d/99dd-build-ca
  for f in /etc/apt/sources.list /etc/apt/sources.list.d/*; do
    [ -f "$f" ] && sed -i 's|http://|https://|g' "$f"
  done
fi
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "$@"
rm -rf /var/lib/apt/lists/* /etc/apt/apt.conf.d/99dd-build-ca /tmp/dd-build-ca.crt
