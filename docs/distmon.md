# DistMon — Detailed Design Document (architecture & component designs)

Below is a full, actionable design for each major component of the DistMon system,

---

# 1. High-level architecture (summary)

DistMon has three logical tiers:

1. **Edge Devices** — iPhone app, embedded Linux agents (Raspberry Pi / Jetson). Responsibilities: capture sensors, on-device privacy filtering, local buffering, secure persistent connection (QUIC), enrollment. 
2. **Backend Fleet & Real-Time Infrastructure** — Signaling server (QUIC), Device Registry, Media Server (ingest/transcoding/SFU), Clips API + Time sync, Update Service, API Gateway. These provide management, real-time viewing, storage, and OTA. 
3. **Data Storage & Analytics** — Object store for blobs, temporal index & metadata DB for queries, time series DB for telemetry, analytics pipelines, admin UI and API. 

All traffic is authenticated & encrypted (mTLS, TLS1.3 over QUIC). Privacy filtering is performed on-device; raw PII imagery never transmitted. 

---

# 2. Component-by-component design

## 2.1 Edge Device Agent (common design)

**Purpose:** unified agent design to run on iOS, Linux embedded, and other custom rigs.

### Responsibilities

* Device capability discovery & report on enrollment. (See capability JSON sample in Section 3). 
* Capture orchestration for video/audio/depth/IMU/GPS/environmental.
* On-device privacy filtering pipeline (person/face/license-plate).
* Local storage buffer + resilient upload logic.
* Persistent QUIC connection for signaling, commands, datagrams.
* OTA update client.
* Health telemetry & watchdog.
* Secure credential store (Keychain / TMP / secure enclave).

### Architecture (modules)

* **Capture Manager** — platform-specific adapters (AVFoundation on iOS, V4L2/GStreamer on Linux). Exposes standardized frame/event stream to rest of agent.
* **Privacy Filter** — runs model inference and applies pixelation/blur/mask. Supports fallback to lower accuracy when resources limited.
* **Recorder / Local Store** — chunked file writer (e.g., fixed-duration segments: 10s/30s) + local metadata index.
* **Uploader / Streamer** — multiplexes into QUIC streams: reliable control stream, RTP-like media stream(s) for recorded chunks, unreliable datagrams for IMU/GPS telemetry.
* **Control Loop** — receives commands (start/stop/config), manages priority modes and schedules.
* **OTA Client** — handles download, verify, A/B write, switch, health check, rollback.
* **Security Module** — certificate management, mTLS handshake logic, secure storage.

### Data formats & chunking

* **Media segments**: fragmented MP4 (fMP4) or WebM segments for adaptive playback; store MD5/sha256 per segment.
* **Metadata envelope** (per segment):

```json
{
  "device_id":"uuid",
  "segment_id":"device-uuid-YYYYmmddTHHMMSSZ-0001",
  "start_ts_utc_ms": 1670000000000,
  "end_ts_utc_ms": 1670000005000,
  "streams":[{"type":"video","codec":"h264","width":1920,"height":1080,"fr":30},{"type":"audio","codec":"opus","sr":48000}],
  "privacy_applied": ["face_blur"],
  "signature": "base64(...)" // agent signs metadata with device key
}
```

### Time stamping

* Stamp at capture source using monotonic clock + UTC system time (see time sync section)

### Resource profiles / perf targets (guidelines)

* iPhone Pro: can do 1080p@30 + CoreML person segmentation in real-time with < 30% CPU on typical devices.
* Jetson Nano/Orin: TensorRT for real-time multi-camera; plan GPU memory + batch sizes accordingly.
* Raspberry Pi: use lower-res or lower FPS or offload heavier privacy ops to NN accelerators (e.g., Coral) or use faster TFLite models.

---

## 2.2 Privacy Filter (detailed)

**Goal:** ensure PII (faces, persons, plates) does not leave device.

### Functionality

* Person/face/plate detection → segmentation/mask → transformation (gaussian blur / pixelate / solid mask).
* Exclusion zones: region retention/obscure rules stored as configuration.
* Configurable policies: which classes to blur and which to preserve; dynamic overrides by admin.

### Model strategy & runtimes

* Provide same-model family exported to:

  * **iOS:** Core ML (.mlmodelc) optimized with quantization & Apple Neural Engine.
  * **Jetson:** ONNX → TensorRT engine with FP16/INT8.
  * **Raspberry Pi:** TFLite with EdgeTPU support if present.
* Fall-back CPU-only lightweight model if acceleration absent.

### Pipeline (per frame)

1. Preprocess (resize, normalize)
2. Detection → bounding boxes + segmentation mask
3. Mask post-process (morphology, smoothing)
4. Apply style (blur/pixelate/solid) with blending (to reduce artifacts)
5. Optional: metadata-only mode — send bounding boxes + blurred/occluded low-res preview for admin

---

## 2.3 QUIC-based Signaling & Transport

**Primary protocol:** QUIC + TLS1.3 (use existing open-source implementations where appropriate, e.g., quiche, msquic, etc.)

### Connection model

* Single QUIC connection per device to Signaling Server (handles control + data).
* Multiplexed QUIC streams:

  * Stream 0: persistent **control channel** (reliable) for RPCs, heartbeats, config.
  * Stream 1+: **media streams** (reliable for file transfer; use stream per logical media)
  * Datagrams: low-latency telemetry (IMU/GPS) using QUIC datagram extension (unreliable).
* Connection migration supported for IP change (WiFi ↔ cellular).

### Message framing & RPC

* Use lightweight protobufs for control RPCs over reliable streams.
* Keep-alive & ping interval: configurable (e.g., 15s). Heartbeat uses small datagram or ping frame.

### Failure modes & reconnection

* Support 0-RTT resumption for quick reconnect.
* Local retention policy: on disconnection, continue local recording; backfill upload when reconnected.

---

## 2.4 Device Enrollment

The enrollment process is the critical bridge that establishes a hardware-backed identity and assigns a device to an organization. DistMon supports three primary enrollment paths.

Regardless of the method used, the result of enrollment is a cryptographically secured identity:

| Feature | Implementation |
| --- | --- |
| **Identity Storage** | Certificates are stored in the **Secure Enclave** (iOS) or **TPM** (Linux). |
| **Trust Anchor** | All devices are pre-loaded with the **Organization's Root Public Key** for offline validation. |
| **Mutual Authentication** | All subsequent QUIC traffic uses **mTLS** (Mutual TLS) with these certificates. |
| **Lockout Policy** | To prevent brute-force attacks on manual codes, devices implement an exponential backoff after 3 failed attempts. |

### Cloud-Assisted Enrollment (Standard)

This method is optimized for devices with an active internet connection and a user interface (e.g., iPhones).

* **Workflow**: The device generates a 6-digit challenge code and displays it to the administrator.
* **Validation**: The administrator enters this code into the Admin Portal, which links the device's hardware ID to the organization.
* **Provisioning**: Once validated, the device polls the backend to receive its long-lived X.509 certificates.

###  Headless Offline Enrollment (Local Portal)

Designed for embedded devices (Jetson, Raspberry Pi) deployed in environments without external internet access. This "Local Access Point" strategy uses the administrator's smartphone as a temporary bridge.

1. **Setup Trigger**: The user initiates "Setup Mode" via a physical interaction (e.g., button hold).
2. **Local Captive Portal**: The device creates a temporary WPA2-secured Wi-Fi Hotspot (SSID: `DistMon_Setup_[ID]`).
3. **Browser Interface**: The Admin connects to this Wi-Fi; a local web server on the device serves a "Provisioning Page."
4. **The Challenge**: The page displays a unique **Challenge Code** derived from the device's hardware-backed Public Key.
5. **Offline Signing**: The Admin enters the Challenge into the DistMon Admin App. The app uses a pre-installed **Organization Private Key** to generate a **Response Code**.
6. **Finalization**: The Admin enters the Response Code into the local browser page. The device verifies the signature locally and completes enrollment.

### Manual Challenge-Response (Air-Gapped)

For the most restrictive environments where even local Wi-Fi is prohibited, enrollment can be performed via manual string entry.

* **Mechanism**: The device outputs a text-based challenge string via its console or display.
* **Verification**: The administrator manually types this string into the Admin App to receive a corresponding response string.
* **Security**: This utilizes HMAC-SHA256 based validation against the **Organization Public Key** pre-baked into the device firmware.

---

## 2.5 Device Registry & Metadata Store

**Purpose:** authoritative inventory, state, and metadata (capabilities, current status, connection history).

### Data model

* `devices` table:

  * `device_id (pk)`, `org_id`, `device_type`, `capabilities_json`, `last_seen`, `status`, `current_version`, `cert_serial`, `location_last_point`, `enrollment_state`
  * `capabilities_json` stores the full report (use the sample JSON shape). 
* `device_health` (time-series) stored in TSDB (Prometheus for metrics, long-term in VictoriaMetrics/InfluxDB)
* Indexing: index by `org_id`, `device_type`, `status`, and support searching by capability attributes.

### APIs

* `GET /v1/devices` (filter by org, status)
* `GET /v1/devices/{device_id}`
* `POST /v1/devices/{device_id}/commands` (enqueue command)

---

## 2.6 Signaling Server

**Purpose:** persistent QUIC endpoint which authenticates devices and routes control messages, coordinates WebRTC sessions for browsers (to distribute received streams - via QUICK - from server to browsers).

### Responsibilities

* Accept QUIC connections, authenticate with mTLS.
* Maintain device-session mapping; route server→device commands.
* Coordinate SFU brokering (if using WebRTC via Media Server).
* Health checks, metrics export.

### Implementation notes

* Use horizontally scalable QUIC servers behind a load balancer that supports UDP load balancing (e.g., L4 LB).
* Sticky session by device_id to allow stateful in-memory maps or use Redis for session store if horizontal scaling requires statelessness.
* Integrate with API Gateway and Device Registry for device ACLs.

---

## 2.7 Media Server & SFU

**Purpose:** ingest media streams, optionally transcode and provide real-time playback via SFU, persist to object storage.

### Features

* Accept media via QUIC or SRTP over QUIC; support H.264/H.265/VP9 and Opus/AAC.
* Optional on-the-fly privacy checks (only metadata/verifying signatures) — actual PII already obscured on device.
* Transcoding for real-time browser delivery (WebRTC) using SFU (e.g., Janus, Jitsi, MediaSoup, or custom GStreamer pipeline).
* Write original segments to object storage (S3-compatible), store metadata to temporal index.

### Ingest pipeline

1. Receive segment/stream
2. Validate metadata & signature
3. Persist raw segment into object storage under path: `/orgs/{org}/devices/{device}/year=YYYY/month=MM/day=DD/segmentId.mp4`
4. Append metadata to Temporal Index DB (see 2.9)
5. Optionally create adaptive bitrate renditions for WebRTC

### Storage & retention

* Base policy: hot (last 30 days) in fast object store + cold storage/archival (Glacier) for longer retention.
* Support retention ACL per org.

---

## 2.8 Time Synchronization

**Requirements:** ±1ms

### Strategy

* **Primary:** NTP configured for rapid sync and correction.
* **High-accuracy for embedded rigs:** PTP (IEEE 1588) where local network supports hardware timestamping.
* **GPS/PPS** as a fallback / primary time source for remote devices with GPS hardware (use for absolute time & PPS pulses).
* **Monotonic clock + wall-clock anchor**: stamp frames with both monotonic timestamp (to order events locally) and UTC wall-clock corrected via NTP/PTP+GPS. Use monotonic to compute relative deltas (<1ms inside session).

### Implementation notes

* On iOS: rely on system time sync and prefer using `AVCapture` frame timestamp features.
* On Linux: use kernel timestamping via `SO_TIMESTAMPING` and robust NTP/PTP config.
* Time-sync telemetry: devices report last sync, offset, jitter metrics to backend; raise alarms if offsets exceed thresholds.

---

## 2.9 Clips API & Temporal Indexing

**Purpose:** allow clients to discover, query, request, and stream/download clips across devices/time ranges. 

### Storage layers

* **Object Store:** stores segments (immutable), organized by org/device/date/segment.
* **Temporal Index (metadata DB):** a searchable index mapping time ranges → segment ids; stores additional metadata (privacy_applied, GPS bounding, event tags).

  * Use a combination of Postgres (for query flexibility) + Elasticsearch/Opensearch for full-text and range queries, or a specialized time-series DB plus a secondary index for random access.
  * Metadata fields: `segment_id`, `device_id`, `start_ts`, `end_ts`, `byte_range`, `s3_path`, `thumbnail_path`, `gps_bbox`, `tags`, `privacy_flags`, `checksum`.

### Query API examples

* `GET /v1/clips?device_id=&from=&to=&min_confidence=&tag=` → returns list of matching segments with metadata.
* `POST /v1/clips/compose` — request server-side stitch/transcode of N segments into a single clip (returns job id).
* `GET /v1/clips/{clip_id}/stream` — returns HLS or progressive stream.
* `GET /v1/clips/{segment_id}/download?range=...` — direct download with byte range.

### Temporal indexing design

* Index segments by start_ts and end_ts; to find segments covering window [t1,t2] query: `WHERE start_ts < t2 AND end_ts > t1`.
* For performance: use time-partitioned tables (daily/monthly) and maintain a TTL background job for expired data (controlled by retention policy).
* Support approximate location search by storing geojson bbox and using spatial index (PostGIS) for geo queries.

### Performance optimizations

* Pre-generate low-res thumbnails + short preview (e.g., 5s) for quick web UI previews.
* Cache stitching results for frequent composite queries.
* Use signed temporary URLs for downloads (S3 presigned URLs).

---

## 2.10 Update Service (OTA)

**Supported update categories**: system images (A/B), agent app, configs, ML models. 

### Flow for embedded (A/B):

1. Backend publishes signed artifact manifest to Update Service.
2. Device queries updates or receives push notification.
3. Device downloads to staging partition.
4. Verify signature + checksum.
5. Flash to inactive partition (B), set boot flag.
6. Reboot; health checks (agent runs smoke tests, sensor checks).
7. If health passes, mark new partition active in registry; else autop rollback to previous.

### Model updates

* Models are versioned and signed; device verifies signature and schema before activation.
* Models stored with compatibility metadata (min-agent-version, hardware-acceleration tags).

### Rollout strategy

* Canary → % rollout → full. Monitor metrics & device health, automatic pause/rollback on anomaly.
* Integrate with Update Service UI to view per-device update status.

### Security

* All packages signed with org/private key + global chain and anti-rollback enforced via monotonic counters or secure TPM storage.

---

## 2.11 API Gateway & Admin Portal

**API Gateway**: ingress point for admin UI and partner integrations; provides authentication, routing, rate limiting.

### Authentication

* OAuth2 for human/admin users; API keys / JWTs for programmatic integrations.
* RBAC with organization scoping.

### Admin Portal features

* Device listing, health dashboard, session map, real-time viewer, update rollout UI, logs & audit trail.
* Map view with last-known device GPS, playback controls, clip search, and privacy policy controls.

### Example endpoints

* `GET /v1/admin/orgs/{org_id}/devices`
* `POST /v1/admin/orgs/{org_id}/devices/{id}/action` (start collection, stop, push update)
* `GET /v1/admin/jobs` (OTA, transcode jobs, etc.)

---

## 2.12 Security & Key Management

**Device authentication:** mutual TLS with device certificates in secure storage (Keychain/TPM) and periodic rotation. 

### PKI

* Internal CA issues device certs at enrollment.
* Certificate rotation policy: rotate certs every N months (configurable).
* Use hardware-backed key storage where available.
* Maintain CRL/OCSP or short-lived certs to revoke compromised devices.

### Data protection

* In-transit: TLS1.3 (QUIC).
* At rest: AES-256 encryption on object store (client-side encryption optional) and DB encryption.

### Access control

* Fine-grained RBAC enforced at API Gateway; org-scoped resources only visible to members with permissions.

### Audit & compliance

* Immutable audit logs for critical admin actions (start/stop recording, privacy rule changes, enrollment actions).
* Integrate with SIEM for monitoring suspicious activity.

---

## 2.13 Observability & Ops

### Metrics & monitoring

* Export metrics via Prometheus (device agent metric endpoint + backend services).
* Dashboards in Grafana for device count, connection latency, time sync offsets, model inference latency, storage ingress rate.

### Logging & tracing

* Centralized logs (structured JSON) into ELK/Opensearch; use OpenTelemetry for traces across services.
* Retention & rotational policies for logs.

---

## 2.14 Deployment & Scaling

Both cloud-hosted and self-hosted setups.

### Cloud-hosted (managed)

* Kubernetes for backend microservices. Use k8s Deployments/StatefulSets.
* Use autoscaling groups for QUIC servers and Media Servers.
* Object storage: S3-compatible (AWS S3 / MinIO).
* Use CDN for serving clips/hls segments.

### Self-hosted

* Provide Helm charts / Kustomize + Terraform modules for setup; Docker Compose for small deployments.
* Provide operators for Update Service and Device Registry.
---

# 3. Data models & sample APIs

### Device capability report

Use this shape as device capability registration payload. 

```json
{
  "device_id": "uuid",
  "device_type": "phone",
  "capabilities": {
    "video": { "cameras": [ { "id":"wide_back", "resolutions":["3840x2160","1920x1080"], "framerates":[60,30], "features":["hdr"] } ]},
    "audio": { "microphones":[ {"id":"mic_0","sample_rates":[48000]} ]},
    "depth": {"sensors":[{"id":"lidar_0","type":"lidar","range_m":[0.5,5.0]}]},
    "sensors": {"imu":{"supported":true,"sample_rate":100},"gps":{"supported":true,"accuracy_m":3.0}},
    "privacy_filter": {"supported":true,"methods":["person_blur","face_blur","pixelate"], "processing":"on_device"}
  }
}
```

### Example REST endpoints

* `POST /v1/devices/register` — device posts capability report → returns enrollment state
* `POST /v1/devices/{id}/commands` — push commands
* `GET /v1/clips?from=..&to=..&device=..` — query clips
* `POST /v1/updates/rollout` — create rollout job

---

# 4. Operational runbooks (short)

### Device offline recovery

* If device disconnected > X hours: notify ops, show last telemetry, queue a remote reboot command. Local buffer keeps recording until space threshold; delete oldest allowed by retention policy.

### Failed OTA rollback

* Auto-rollback if health check fails; alert Ops with device logs and snapshot of failing partition.

### Privacy filter failure

* If model inference crashes or exceeds latency threshold, agent switches to safe-mode: immediately stop sending video, or send low-resolution privacy-only sentinel (depending on org policy), and escalate.

---

# 5. Testing & validation

### Unit & integration testing

* Unit tests for agent capture adapters (mock sensors).
* End-to-end tests with test devices using simulated network conditions (to test QUIC migration, packet loss).
* Model validation: accuracy and FPS benchmarks per target hardware.

### Load testing

* Simulate thousands of devices connecting to Signaling Server and performing uploads to measure throughput and scaling curves.

### Security testing

* Test access control to all exposed APIs with different users and roles
* Tests on enrollment, certificate issuance, API Gateway.
* Verify anti-rollback and signature checks.

---

# 6. Roadmap 

Implement functionality according to the following roadmap. Each phase should include extensive unit tests and documentation of the implemented functionality. It should also include a code review and approval of the functionality implemented.

| Phase | Focus | Key Deliverable |
|-------|-------|-----------------|
| **1. Foundation** | QUIC + enrollment + basic capture | Devices stream raw video |
| **2. Privacy & Multi-Sensor** | On-device blur, all sensors | Privacy-filtered multi-sensor |
| **3. Time Sync & Clips** | NTP sync, clips API | Query clips by time range |
| **4. OTA Updates** | A/B partitions, staged rollouts | Remote fleet updates |
| **5. Advanced Enrollment** | QR, BLE, admin tools | Flexible setup + dashboard |
| **6. Scale & Polish** | WebRTC, CDN, hardening | Production ready |

---

# 7. Appendix — Key design decisions / rationale

* **On-device privacy**: required by spec to prevent PII leave; use signed models and hardware-backed keys to ensure integrity. 
* **QUIC**: chosen for connection migration, multiplexing, and datagram support to meet heterogeneous network requirements. 
* **A/B OTA for embedded**: atomic updates and automatic rollback minimize bricking risk. 



