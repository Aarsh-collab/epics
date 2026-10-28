# Project-owned image: the shared TRELLIS.2 image plus the mesh-repair dependency of the outer-wall
# cleanup. The base image itself is never modified. Built by up.sh as epics-app:repair.
FROM trellis2:flashattn-render
RUN /opt/venvs/trellis2/bin/pip install --no-cache-dir "manifold3d==3.5.4" \
 && /opt/venvs/trellis2/bin/python -c "import manifold3d"
