# AllSpark Edge Server: Architecture & Expanding Role

> **Status:** Exploratory / Design Phase
> This document formalizes the design goals and future direction of the AllSpark Edge Server, transitioning from a simple data receiver into a robust, multi-protocol orchestration hub.

## 1. Core Responsibilities & Objectives

The edge server's role is expanding to manage a diverse ecosystem of clients. Its primary responsibilities include:

1. **Active Client Monitoring**: Track connection health, heartbeat ping/pong, and lifecycle events for all registered clients.
2. **Configuration Management**: Serve as the source of truth for client policies. For example, pushing down `communicationsPolicy` to iOS devices to enforce security postures.
3. **Out-of-Band (OOB) Connection Boosting**: Manage fallback and escalation of connection types based on environment and urgency.
4. **Authentication & Authorization (RBAC)**: Handle user auth, issue time-bound tokens, and enforce role-based access to the signaling plane and data vault.
5. **Orchestration & Rule Engine**: Execute scripts and rules to route messages, trigger events, and coordinate multi-device workflows (see Section 3).

## 2. Client Ecosystem

The edge server manages three primary classes of clients:

*   **Mobile Clients (iOS, future Android)**: Human-operated endpoints for real-time video/audio capture, privacy filtering, and local buffering.
*   **Data Capture Rigs (NUC, Pi, Jetson)**: Local, industrial compute nodes. These "headless" rigs listen to communication buses, independent cameras, mics, and industrial MQTT data planes for error states.
*   **Agentic Frameworks**: AI-driven analysis clients. These agents receive data packages (e.g., video + error logs), perform analysis, and return actionable feedback to the server for human review.

## 3. Orchestration Example: Industrial Error Loop

The rule engine enables complex, event-driven orchestration across all three client classes.

**Workflow:**
1. **Trigger**: An industrial production line control plane emits an error over MQTT.
2. **Detection**: A Data Capture Rig (NUC) listening to that bus detects the error and forwards the alert to the Edge Server.
3. **Evaluation**: The Edge Server's script evaluates the severity.
4. **Action**: The Server signals the NUC (or nearby mobile apps) to capture and upload a video time-range surrounding the error event.
5. **Analysis**: The Server packages the industrial error logs with the uploaded video and dispatches it to an Agentic Client.
6. **Closing the Loop**: The Agent analyzes the footage, identifies the anomaly, and sends feedback back to the server, which stores it for human review.

## 4. Communications Protocol & Platform Strategy

To support low-latency signaling alongside high-volume data transport across an intranet worksite, we are exploring well-supported, off-the-shelf open platforms to replace custom legacy patterns (e.g., pure WebSockets).

### Signaling (Low-Latency, 2-Way)
*   **Target Platforms**: **MQTT** (Primary) or **NATS**.
*   **Recommendation**: **MQTT** is the recommended choice for cross-platform signaling. It is purposefully designed for edge scenarios, gracefully handles spotty connections, and has robust, mature client libraries across iOS (CocoaMQTT) and Android (Eclipse Paho). This fits perfectly into the Industrial Error Loop orchestration.
*   **Purpose**: Lightweight pub/sub for control planes, hardware error messaging, rule triggers, and maintaining live device registries.
*   **Fallback**: WebSocket is maintained for simple browser-client integration.

### Data Transport (High-Volume, 2-Way)
*   **Target Platforms**: **QUIC** (HTTP/3) (Primary) or **gRPC**.
*   **Recommendation**: **QUIC** (HTTP/3) is the recommended primary choice for high-volume, multiplexed data transfer. It natively avoids head-of-line blocking, making it exceptionally resilient on spotty edge networks when concurrently sending video, depth, and audio streams. **gRPC** remains a strong secondary option for strict RPC coordination where specific typed API contracts are preferred.
    *   **Media Addendum**: For purely live video/audio streaming, **WebRTC** is the recommended standard as it natively handles packet loss, adaptive bitrates, and has full native cross-platform support.
*   **Purpose**: Resilient, multiplexed binary transfer. QUIC avoids head-of-line blocking for sending multiple concurrent video/depth/audio streams. gRPC provides strong typing for RPC coordination.
*   **Legacy**: Standard HTTP chunked uploads for simple, large-file ingest.

### Mobile Client Protocol Support Levels

Both iOS and Android provide robust support for these communication protocols. HTTP and WebSocket are well-established, while QUIC is fully supported on modern OS versions, offering unparalleled performance benefits.

#### QUIC Protocol Support
| Platform | Support Level | Details |
| :--- | :--- | :--- |
| **iOS** | Supported | QUIC (HTTP/3) is enabled by default in `URLSession` (iOS 15+). Custom raw QUIC streams can be built via `Network.framework` (iOS 15+). |
| **Android** | Supported | QUIC is available via the Cronet library (Android API 29+ / Android 10+ recommended for best support). |

#### HTTP Protocol Support
| Platform | Support Level | Details |
| :--- | :--- | :--- |
| **iOS** | Full Support | HTTP/1.1 and HTTP/2 are fully supported, with HTTP/3 (which uses QUIC) being gradually adopted. |
| **Android** | Full Support | HTTP/1.1 and HTTP/2 are fully supported, with HTTP/3 support available through libraries like Cronet. |

#### WebSocket (WS) Support
| Platform | Support Level | Details |
| :--- | :--- | :--- |
| **iOS** | Full Support | WebSocket is fully supported, allowing real-time communication in apps. |
| **Android** | Full Support | WebSocket is fully supported, enabling real-time data exchange in applications. |

#### WebRTC Support
| Platform | Support Level | Details |
| :--- | :--- | :--- |
| **iOS** | Full Support | Natively supported via WebKit for web apps and available via Google's WebRTC library for native apps. |
| **Android** | Full Support | Natively supported in WebView and fully supported via Google's WebRTC library for native implementations. |

#### gRPC Support
| Platform | Support Level | Details |
| :--- | :--- | :--- |
| **iOS** | Full Support | Supported via the official gRPC Swift or Objective-C libraries, leveraging HTTP/2. |
| **Android** | Full Support | Fully supported using gRPC-Java, enabling strong typing and efficient multiplexed RPC coordination. |

#### MQTT Support
| Platform | Support Level | Details |
| :--- | :--- | :--- |
| **iOS** | Full Support | Supported via robust third-party client libraries (e.g., CocoaMQTT) for low-latency pub/sub messaging. |
| **Android** | Full Support | Supported via established client libraries like Eclipse Paho for lightweight IoT signaling. |

## 5. Deployment Strategy (Intranet Context)

The server must operate reliably in zero-trust, air-gapped, or intranet worksite environments.

1. **Containerization**:
   The monolith should be segmented into scalable Docker containers based on logical roles (e.g., `auth-service`, `signaling-broker`, `media-ingest`, `rule-engine`). This ensures easy colocation and independent scaling.

2. **Co-location / Edge-Native**:
   Portions of the edge server architecture can be co-located directly on the Data Capture Rigs (NUC). By running a local NATS/MQTT broker on the NUC, critical machine-to-machine signaling avoids unnecessary intranet hops, syncing to a primary cluster only when needed.

---
*For a snapshot of previous architectural explorations prioritizing secure capture, see [DistMon Design](distmon.md).*
