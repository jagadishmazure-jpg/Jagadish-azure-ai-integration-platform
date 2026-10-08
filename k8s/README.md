# k8s

Kubernetes manifests for the opt-in AKS compute profile (`computeProfile = 'aks'`). They run the same fifteen workloads, commands and service names as the Container Apps profile, generated from the workload list in [`infra/main.bicep`](../infra/main.bicep) by [`scripts/render_k8s.py`](../scripts/render_k8s.py). CI checks they are current and validates them offline with kubeconform (strict, Kubernetes 1.33 schemas). **Never applied to a cluster.**

| File | What it does |
|---|---|
| [`README.md`](README.md) | This file |
| [`kustomization.yaml`](kustomization.yaml) | Lists every manifest, so `kubectl apply -k k8s` would apply the set |
| [`namespace.yaml`](namespace.yaml) | Namespace `aiip` enforcing the `restricted` Pod Security Standard, and the `aiip-azure` ConfigMap (Azure endpoints, left empty: filled from the IaC outputs at deploy time) |
| [`network-policies.yaml`](network-policies.yaml) | Default deny for ingress and egress, then allow rules: DNS to kube-dns, workload to workload on 8080 inside the namespace, the ingress controller namespace to the four edge gateways only, and egress on 443 and 5671 (AMQP) for Azure services, with the instance metadata endpoint excluded |
| [`workloads/`](workloads/README.md) | One file per workload: ServiceAccount, Deployment, Service (HTTP workloads) and PodDisruptionBudget |

Every Deployment runs as UID 10001 with `runAsNonRoot`, a read-only root filesystem (an `emptyDir` on `/tmp`), no privilege escalation, all capabilities dropped and the `RuntimeDefault` seccomp profile, with CPU and memory requests and limits matching the Container App size (0.5 vCPU, 1 GiB). HTTP workloads run two replicas with readiness and liveness probes on `/healthz` and a PodDisruptionBudget of `minAvailable: 1`.

Not covered (would be needed before a real AKS rollout):

- The image is a placeholder (`registry.example/aiip-platform:replace-me`); set it to the ACR image by digest.
- The ServiceAccount client-id annotations are placeholders. The IaC enables the OIDC issuer and Workload ID on the cluster, but does not create the federated identity credentials that bind each ServiceAccount to its managed identity.
- The workers have no KEDA `ScaledObject` (Container Apps scales them on queue length); they run one replica.
- No Ingress or Gateway resource. The edge allow rule assumes the AKS application routing add-on (namespace `app-routing-system`), which the IaC does not enable.
- NetworkPolicy cannot filter by FQDN, so egress on 443 and 5671 is open to any address; pair it with Azure Firewall or private endpoints to narrow it.

Regenerate after changing the workload list: `python scripts/render_k8s.py`.
