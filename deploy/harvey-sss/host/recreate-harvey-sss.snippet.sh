#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-only
# Extra bind mounts only. Parent must reuse the existing harvey-sss:v3
# recreate command (uid 1000, sleep infinity) and KEEP the three current mounts:
#   /opt/harvey-sss/data            -> /app/data              RW
#   /opt/harvey-sss/harvey.local.yaml -> /app/harvey.local.yaml RW
#   /opt/harvey-sss/claude-home     -> /home/harvey/.claude   RW
# Add:
#   /opt/sss-glm-channel/requests   -> /var/sss-glm/requests  RW
#   /opt/sss-glm-channel/responses  -> /var/sss-glm/responses RO
#
# Hermes (narrow recreation or extra mounts), uid hermes 1000:
#   /opt/sss-glm-channel/requests   -> /var/sss-glm/requests  RO
#   /opt/sss-glm-channel/responses  -> /var/sss-glm/responses RW
#   /opt/sss-glm-channel/ledger     -> /var/sss-glm/ledger    RW
# Ledger is never mounted into harvey-sss.
#
# Do not pass --privileged, do not mount docker.sock into SSS,
# do not copy OpenRouter/Hermes/DMTC keys into SSS.

echo "--mount type=bind,src=/opt/sss-glm-channel/requests,dst=/var/sss-glm/requests,bind-propagation=rprivate"
echo "--mount type=bind,src=/opt/sss-glm-channel/responses,dst=/var/sss-glm/responses,readonly,bind-propagation=rprivate"
echo
echo "# hermes-agent extra mounts:"
echo "--mount type=bind,src=/opt/sss-glm-channel/requests,dst=/var/sss-glm/requests,readonly,bind-propagation=rprivate"
echo "--mount type=bind,src=/opt/sss-glm-channel/responses,dst=/var/sss-glm/responses,bind-propagation=rprivate"
echo "--mount type=bind,src=/opt/sss-glm-channel/ledger,dst=/var/sss-glm/ledger,bind-propagation=rprivate"
