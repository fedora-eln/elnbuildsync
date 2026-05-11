{{/*
Fragment for the optional Postgres testing sidecar (native initContainer + restartPolicy Always).
NOT for production use—see values.yaml and NOTES.txt.
*/}}
{{- define "elnbuildsync.postgresContainerBody" -}}
image: {{ .Values.sidecar_database_postgres.image | quote }}
imagePullPolicy: IfNotPresent
env:
- name: POSTGRES_USER
  value: {{ .Values.sidecar_database_postgres.user | quote }}
- name: POSTGRES_DB
  value: {{ .Values.sidecar_database_postgres.database | quote }}
- name: POSTGRES_PASSWORD
  valueFrom:
    secretKeyRef:
      name: ebs-config
      key: ebs_db_pw
ports:
- containerPort: 5432
  protocol: TCP
{{- if .Values.sidecar_database_postgres.resources }}
resources:
  {{- toYaml .Values.sidecar_database_postgres.resources | nindent 2 }}
{{- else }}
resources: {}
{{- end }}
startupProbe:
  exec:
    command:
    - pg_isready
    - -U
    - {{ .Values.sidecar_database_postgres.user | quote }}
    - -d
    - {{ .Values.sidecar_database_postgres.database | quote }}
  periodSeconds: 5
  failureThreshold: 30
livenessProbe:
  exec:
    command:
    - pg_isready
    - -U
    - {{ .Values.sidecar_database_postgres.user | quote }}
    - -d
    - {{ .Values.sidecar_database_postgres.database | quote }}
  initialDelaySeconds: 30
  periodSeconds: 20
  timeoutSeconds: 5
readinessProbe:
  exec:
    command:
    - pg_isready
    - -U
    - {{ .Values.sidecar_database_postgres.user | quote }}
    - -d
    - {{ .Values.sidecar_database_postgres.database | quote }}
  periodSeconds: 5
  timeoutSeconds: 3
volumeMounts:
# Postgres 18+ docker-library images expect the volume at /var/lib/postgresql
# (not .../data); see https://github.com/docker-library/postgres/pull/1259
- name: postgres-data
  mountPath: /var/lib/postgresql
{{- end }}
