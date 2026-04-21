# Changelog


## [0.5.0](https://github.com/WiseLabCMU/AllSpark-edge-server/compare/v0.4.0...v0.5.0) (2026-04-21)


### Features

* **agent_service:** implement agent integration with anomaly analysis and response storage ([d0c3bf7](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/d0c3bf7c553288d23440d964eddd87cc29a32422))
* **agent:** update user ID to 'user' and adjust session creation logic for ADK compatibility ([c74d62e](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/c74d62eaaf9f8486a34b7aa199c5de04a90141ee))
* **control_plane:** modernize edge UI, harden config loading, and optimize polling ([7d98836](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/7d9883639b180707e9a79776bba0aad243908993))
* **control-plane:** reorganzied using yaml, parallel servers ([d625f20](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/d625f20cabbfe11216d4bd2c04e3768ca2941dfa))
* **debug:** add manual anomaly trigger page for testing and development ([9106145](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/91061450d42ebf60704bcddae083651b27d723bb))
* implement file watcher and reviewer ([a511764](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/a511764e2be93eda83a508300bbfbc114ba1af72))
* load sidecar old server on separate port ([9c9a387](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/9c9a387b5f493f864240add5b01f5c84ffa0b3e4))
* **logs:** add manual investigations to anomalies ([65b2ef8](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/65b2ef83e81720e8b76f936b3f029cd3a364a750))
* **nicegui:** add control plane dashboard in py-rich nicegui ([c457251](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/c457251aae6d2d95bb3ddd94a48fd08800068767))
* **nicegui:** add settings editing integration ([f097b19](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/f097b19ddd6b38361dc71148b4f6a6c42ab4a118))
* restore /logs filter/preview ([1f5df65](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/1f5df655fbfd61760d5e5024860fe0f058423d38))


### Bug Fixes

* consolidate split configuration into unified python/config.yaml ([34a07ec](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/34a07ecb0b7227dcfc57550793a1673312ca793b))
* **control:** restore old mobile index ([c2928d8](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/c2928d8f24d1f749efdd2768fecf4b2d3b52ecf8))
* edge server api should self-detect its protocol ([32d8554](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/32d855416b45482a8661a993873b8ef83236d5b3))
* export rerun/secret to config ([defe9c5](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/defe9c5abf905651be91979704b3c7940b46120b))
* migrate to config.yaml, remove redundant files ([2147b80](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/2147b80c4e0856be665c20da961c10a2b4c69688))
* Parse ms scale timestamps accurately preventing year out-of-range crash ([e4cc103](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/e4cc1037aca392136603d7cff02428b3065dbaf3))
* **python:** add bootstrap config.json if missing ([2b4cc1b](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/2b4cc1b73a1ff6c4692e68377a94d00268dd7b63))
* show active tab, slight reroder ([fea2946](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/fea29462718504351b40f537d330845b9551f9d4))
* update release please to latest ([c58466a](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/c58466ab5fc511da8d1d0fa9859411b7142f4c22))
* update settings with full config.yaml ([86b2c73](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/86b2c73897b49a6a329d5ae7fcad1292d45577d4))

## [0.4.0](https://github.com/WiseLabCMU/AllSpark-edge-server/compare/v0.3.0...v0.4.0) (2026-03-10)


### Features

* **comms:** add communications policy to define which channels should be allowed ([c95f433](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/c95f4332e9d40eefbc60bb4be0c5536f2fea1a50))


### Bug Fixes

* upload folders save as org/device/date structure ([c4b6434](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/c4b6434a0c523cc8bc0f5fd1fbf4d23c432b7c54))

## [0.3.0](https://github.com/WiseLabCMU/AllSpark-edge-server/compare/v0.2.0...v0.3.0) (2026-02-20)


### Features

* **bonjour:** add .local allspark server discovery ([1320084](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/1320084e19603f1f0d4a246e81f28a60019f4532))
* **camera:** added auto record of video chunks and remote recall ([306a7af](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/306a7afa4eda43e0c6b4fc94a1f6f0388daaa414))
* **client:** add list of device interfaces for debug ([aa40fc1](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/aa40fc14c053ed5a93e0d189af0edf8eaa7a8c67))
* **server:** add qrcode scan for alternate out of band setup ([5522926](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/5522926e18e1e81df73b51c2f1fd57f79d25796d))
* **server:** added python implementation of server ([49298a1](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/49298a140d447b48132c512ca664fa645625133e))
* **server:** make general client settings config at server level ([5f5f301](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/5f5f30152b548fb02c06576b83411f3cabdc9527))
* **video:** add video storage limit monitor ([23322a9](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/23322a9bc1c2d060fbab437a2916c701142e7b64))
* **ws:** allow camera view to control ws connect/disconnect ([189c67a](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/189c67aa46e2774008e0f41770eecf2f927400d6))


### Bug Fixes

* add release-please version marker for python script ([44b4c1b](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/44b4c1b6493c93a9dbe6ef7a40a8a42a23494322))
* **config:** perform deep merge from default config ([7bf43e7](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/7bf43e771bc55cefb021ff976128d79f28779c5c))
* **server:** keep only one set of default settings ([e1aeab2](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/e1aeab2cb6dcc3ebb8cf39db549506f780849b9d))
* **upload:** convert file upload and tests to websockets ([0e6cfd4](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/0e6cfd4d8fefa0a7487efd7d429ee5692424d509))
* use config instead of inline for release-please ([b9ebfe0](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/b9ebfe081aa47bbf9efb9d4a03dd1da5a3e11ab4))
* **video:** handle multiple file uploads asynchronously ([d615f80](https://github.com/WiseLabCMU/AllSpark-edge-server/commit/d615f8094287b063382d355b5d78e3707966c6f8))

## [0.2.0](https://github.com/WiseLabCMU/AllSpark-ios/compare/v0.1.0...v0.2.0) (2026-01-23)


### Features

* adding video file save of blurred video ([6889772](https://github.com/WiseLabCMU/AllSpark-ios/commit/688977254ef536bd6a9122d59a3fb1c52cf3992a))
* **audio:** add audio recording to video file capture ([7f10ff2](https://github.com/WiseLabCMU/AllSpark-ios/commit/7f10ff2a1d9b9561f9995c5c78d993d946135298))
* **ws:** allow camera view to control ws connect/disconnect ([cc722d4](https://github.com/WiseLabCMU/AllSpark-ios/commit/cc722d41ca3022ed8b8fbcb51b90574d09db55a1))


### Bug Fixes

* **upload:** convert file upload and tests to websockets ([740d132](https://github.com/WiseLabCMU/AllSpark-ios/commit/740d132fe8e7ba03cf56fc8277b4ece95abe7753))

## [0.1.0](https://github.com/WiseLabCMU/AllSpark-ios/compare/v0.0.1...v0.1.0) (2025-12-03)


### Features

* added privacy filter ([682841a](https://github.com/WiseLabCMU/AllSpark-ios/commit/682841acf0ee0e148f6f8bf1759b6e717f553513))
