{{/*
Names are intentionally static rather than release-derived: they are referenced
by string from jupyterhub.proxy.service.annotations and jupyterhub.ingress
.annotations, which live in the subchart's values and cannot be templated.
*/}}

{{- define "airflow-dev-platform.backendConfigName" -}}
airflow-dev-platform-hub
{{- end -}}

{{- define "airflow-dev-platform.frontendConfigName" -}}
airflow-dev-platform-frontend
{{- end -}}

{{- define "airflow-dev-platform.certificateName" -}}
airflow-dev-platform-cert
{{- end -}}

{{- define "airflow-dev-platform.labels" -}}
app.kubernetes.io/name: airflow-dev-platform
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{/*
Fail early and loudly on a configuration that would produce an unprotected or
unreachable platform, rather than at first login.
*/}}
{{- define "airflow-dev-platform.validate" -}}
{{- if and .Values.platform.gke.enabled .Values.platform.aws.enabled }}
  {{- fail "platform.gke.enabled and platform.aws.enabled are both true; enable exactly one cloud" }}
{{- end }}
{{- if or .Values.platform.gke.enabled .Values.platform.aws.enabled }}
  {{- if not .Values.platform.domain }}
    {{- fail "platform.domain must be set when a cloud ingress is enabled (use scripts/deploy.sh --domain)" }}
  {{- end }}
  {{- if not (index .Values.jupyterhub.ingress "hosts") }}
    {{- fail "jupyterhub.ingress.hosts is empty; it must contain platform.domain" }}
  {{- end }}
  {{- if not (has .Values.platform.domain .Values.jupyterhub.ingress.hosts) }}
    {{- fail (printf "platform.domain (%s) is not in jupyterhub.ingress.hosts (%v); the certificate would not cover the ingress" .Values.platform.domain .Values.jupyterhub.ingress.hosts) }}
  {{- end }}
{{- end }}
{{- if .Values.platform.aws.enabled }}
  {{- $annotations := default dict .Values.jupyterhub.ingress.annotations }}
  {{- if not (index $annotations "alb.ingress.kubernetes.io/certificate-arn") }}
    {{- fail "AWS needs an ACM certificate: set the alb.ingress.kubernetes.io/certificate-arn ingress annotation (scripts/deploy.sh --cloud aws --certificate-arn <arn>)" }}
  {{- end }}
{{- end }}
{{- if contains "REPLACE_ME" .Values.jupyterhub.singleuser.image.name }}
  {{- fail "jupyterhub.singleuser.image.name still points at the placeholder registry; set it to your container registry image (Artifact Registry or ECR; scripts/deploy.sh --image)" }}
{{- end }}
{{- if contains "@sha256:" .Values.jupyterhub.singleuser.image.name }}
  {{/*
     z2jh renders the workspace image as "{name}:{tag}" and drops the colon only
     when tag is empty, so a digest has to sit alone in `name`. Its image-puller
     templates interpolate "{name}:{tag}" with no such handling, which would
     emit an unparseable reference. Refuse the combination rather than ship a
     DaemonSet that CrashLoops on ImagePullBackOff.
  */}}
  {{- if .Values.jupyterhub.singleuser.image.tag }}
    {{- fail "jupyterhub.singleuser.image.name holds a digest, so jupyterhub.singleuser.image.tag must be empty" }}
  {{- end }}
  {{- if or .Values.jupyterhub.prePuller.hook.enabled .Values.jupyterhub.prePuller.continuous.enabled }}
    {{- fail "a digest image reference is incompatible with the z2jh image pre-puller; set jupyterhub.prePuller.hook.enabled=false and jupyterhub.prePuller.continuous.enabled=false, or deploy an immutable tag instead (see docs/operations.md)" }}
  {{- end }}
{{- end }}
{{- end -}}
