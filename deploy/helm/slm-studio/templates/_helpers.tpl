{{/* ------------------------------------------------------------------ names */}}
{{- define "slm.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 40 | trimSuffix "-" -}}
{{- end -}}

{{/* Kept short (36) so suffixes such as "-vllm-<model>-headless" stay under the 63 char DNS label limit. */}}
{{- define "slm.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 36 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 36 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 36 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "slm.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* ------------------------------------------------------------------ labels */}}
{{/* root context -> labels common to every object */}}
{{- define "slm.labels" -}}
helm.sh/chart: {{ include "slm.chart" . }}
app.kubernetes.io/name: {{ include "slm.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: slm-studio
{{- with .Values.global.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{/* dict root, component, tier -> object/pod labels (includes the selector labels) */}}
{{- define "slm.componentLabels" -}}
{{ include "slm.labels" .root }}
app.kubernetes.io/component: {{ .component }}
slm-studio.io/tier: {{ .tier }}
{{- end -}}

{{/* dict root, component -> immutable selector labels */}}
{{- define "slm.selectorLabels" -}}
app.kubernetes.io/name: {{ include "slm.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/* ------------------------------------------------------------------ images */}}
{{/* dict root, image{name,tag,digest}, [name] */}}
{{- define "slm.image" -}}
{{- $root := .root -}}
{{- $img := default (dict) .image -}}
{{- $name := default .name $img.name -}}
{{- $tag := default (default $root.Chart.AppVersion $root.Values.global.imageTag) $img.tag -}}
{{- $prefix := trimSuffix "/" (printf "%s/%s" $root.Values.global.imageRegistry $root.Values.global.imageRepositoryPrefix | trimPrefix "/") -}}
{{- if $img.digest -}}
{{- printf "%s/%s@%s" $prefix $name $img.digest -}}
{{- else -}}
{{- printf "%s/%s:%s" $prefix $name $tag -}}
{{- end -}}
{{- end -}}

{{- define "slm.pullSecrets" -}}
{{- with .Values.global.imagePullSecrets }}
imagePullSecrets:
{{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/* ------------------------------------------------------------------ secrets */}}
{{- define "slm.secretName" -}}
{{- default (printf "%s-secrets" (include "slm.fullname" .)) .Values.secrets.existingSecret -}}
{{- end -}}

{{- define "slm.migrateSecretName" -}}
{{- default (printf "%s-migrate" (include "slm.fullname" .)) .Values.migration.existingSecret -}}
{{- end -}}

{{/* ------------------------------------------------------------------ merging */}}
{{/* dict root, name, cfg -> JSON: serviceDefaults (top-level keys) overridden by the service entry. Use with fromJson.
     Numbers become float64 after the round trip: pipe through `int` before comparing/printing with %d. */}}
{{- define "slm.svc" -}}
{{- $out := dict "name" .name -}}
{{- range $k, $v := .root.Values.serviceDefaults }}{{- $_ := set $out $k $v }}{{- end -}}
{{- range $k, $v := .cfg }}{{- $_ := set $out $k $v }}{{- end -}}
{{- toJson $out -}}
{{- end -}}

{{/* dict root, name, cfg -> JSON: vllm.modelDefaults overridden by the model entry; `args` merged key by key. */}}
{{- define "slm.vllm.model" -}}
{{- $d := .root.Values.vllm.modelDefaults -}}
{{- $out := dict "name" .name -}}
{{- range $k, $v := $d }}{{- $_ := set $out $k $v }}{{- end -}}
{{- range $k, $v := .cfg }}{{- $_ := set $out $k $v }}{{- end -}}
{{- $args := dict -}}
{{- range $k, $v := $d.args }}{{- $_ := set $args $k $v }}{{- end -}}
{{- range $k, $v := (default (dict) .cfg.args) }}{{- $_ := set $args $k $v }}{{- end -}}
{{- $_ := set $out "args" $args -}}
{{- toJson $out -}}
{{- end -}}

{{/* ------------------------------------------------------------------ pod building blocks */}}
{{/* dict p (probe map) -> httpGet probe; every key except path/port is passed through */}}
{{- define "slm.probe" -}}
httpGet:
  path: {{ .path }}
  port: {{ default "http" .port }}
{{- range $k, $v := omit . "path" "port" }}
{{ $k }}: {{ $v }}
{{- end }}
{{- end -}}

{{/* dict root, selector (map) -> topologySpreadConstraints + affinity */}}
{{- define "slm.scheduling" -}}
{{- $root := .root -}}
{{- $sel := .selector -}}
{{- with $root.Values.scheduling.topologySpread }}
topologySpreadConstraints:
{{- range . }}
  - maxSkew: {{ .maxSkew }}
    topologyKey: {{ .topologyKey }}
    whenUnsatisfiable: {{ .whenUnsatisfiable }}
    labelSelector:
      matchLabels:
        {{- toYaml $sel | nindent 8 }}
{{- end }}
{{- end }}
{{- if ne $root.Values.scheduling.podAntiAffinity "none" }}
affinity:
  podAntiAffinity:
  {{- if eq $root.Values.scheduling.podAntiAffinity "hard" }}
    requiredDuringSchedulingIgnoredDuringExecution:
      - topologyKey: kubernetes.io/hostname
        labelSelector:
          matchLabels:
            {{- toYaml $sel | nindent 12 }}
  {{- else }}
    preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        podAffinityTerm:
          topologyKey: kubernetes.io/hostname
          labelSelector:
            matchLabels:
              {{- toYaml $sel | nindent 14 }}
  {{- end }}
{{- end }}
{{- end -}}

{{/* dict root, name, svc -> container env list for a control-plane service */}}
{{- define "slm.env" -}}
{{- $root := .root -}}
{{- $svc := .svc -}}
- name: PORT
  value: {{ $svc.port | int | quote }}
- name: POD_NAME
  valueFrom:
    fieldRef:
      fieldPath: metadata.name
- name: POD_NAMESPACE
  valueFrom:
    fieldRef:
      fieldPath: metadata.namespace
- name: OTEL_SERVICE_NAME
  value: {{ printf "slm-%s" .name | quote }}
- name: OTEL_RESOURCE_ATTRIBUTES
  value: "k8s.pod.name=$(POD_NAME),k8s.namespace.name=$(POD_NAMESPACE)"
- name: HOME
  value: /tmp
{{- range $k, $v := $svc.env }}
- name: {{ $k }}
  value: {{ $v | quote }}
{{- end }}
{{- range $svc.secretEnv }}
- name: {{ . }}
  valueFrom:
    secretKeyRef:
      name: {{ include "slm.secretName" $root }}
      key: {{ . }}
{{- end }}
{{- range $svc.optionalSecretEnv }}
- name: {{ . }}
  valueFrom:
    secretKeyRef:
      name: {{ include "slm.secretName" $root }}
      key: {{ . }}
      optional: true
{{- end }}
{{- end -}}

{{/* dict weight, [deletePolicy], [events] -> hook annotations shared by the migration resources */}}
{{- define "slm.hookAnnotations" -}}
"helm.sh/hook": {{ default "pre-install,pre-upgrade" .events }}
"helm.sh/hook-weight": {{ .weight | quote }}
"helm.sh/hook-delete-policy": {{ default "before-hook-creation" .deletePolicy }}
{{- end -}}
