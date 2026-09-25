"""PromQL building blocks shared by every metrics view.

Both the tier dashboard (``constants.prometheus``) and the windowed metrics
(``constants.metrics_queries``) map pod-level series to their Deployment the
same way; that join lives here once.
"""

from __future__ import annotations

# Pod → ReplicaSet → Deployment: appended to a pod-level expression, it adds a
# ``deployment`` label (kube-state-metrics owner series).
POD_TO_DEPLOYMENT = (
    ' * on(namespace, pod) group_left(replicaset)'
    ' label_replace(kube_pod_owner{owner_kind="ReplicaSet"},'
    ' "replicaset", "$1", "owner_name", "(.*)")'
    ' * on(namespace, replicaset) group_left(deployment)'
    ' label_replace(kube_replicaset_owner{owner_kind="Deployment"},'
    ' "deployment", "$1", "owner_name", "(.*)")'
)

# Application containers. The windowed metrics also drop series with an empty
# ``image`` (cgroup roll-ups); the tier dashboard has always counted without
# that filter, and keeps doing so so its numbers do not shift.
APP_CONTAINERS = 'container!="",container!="POD"'
REAL_CONTAINERS = 'container!="",container!="POD",image!=""'

BYTES_PER_GIB = 1073741824


def by_deployment(inner: str) -> str:
    """Sum a pod-level expression per (namespace, deployment)."""

    return f"sum by (namespace, deployment) ({inner}{POD_TO_DEPLOYMENT})"
