# GenieACS (TR-069 ACS) built from the official npm package, run as the unprivileged "node" user.
# One image, four services (cwmp, nbi, fs, ui) selected by the compose command.
FROM node:22-bookworm-slim
ARG GENIEACS_VERSION=1.2.16
ENV NODE_ENV=production GENIEACS_EXT_DIR=/opt/genieacs/ext
RUN npm install -g --omit=dev "genieacs@${GENIEACS_VERSION}" \
 && npm cache clean --force \
 && mkdir -p /opt/genieacs/ext \
 && chown -R node:node /opt/genieacs
USER node
WORKDIR /opt/genieacs
EXPOSE 7547 7557 7567 3000
