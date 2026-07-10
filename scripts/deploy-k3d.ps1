param(
    [string]$ReleaseName = "milo",
    [string]$Namespace = "agent-zone",
    [string]$ImageTag = "latest"
)

$ErrorActionPreference = "Stop"

helm upgrade --install $ReleaseName charts/milo `
    --namespace $Namespace `
    --create-namespace `
    --set image.tag=$ImageTag
