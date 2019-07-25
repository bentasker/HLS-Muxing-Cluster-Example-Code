HLS Muxing Cluster Example Code
=================================

There's no expectation that you could just download and run this code, as it relies on communication with my C&C.

This repo exists purely to provide the code referenced in the article [Building a HLS Muxing Raspberry Pi Cluster](https://www.bentasker.co.uk/documentation/linux/474-building-a-hls-muxing-raspberry-pi-cluster).

It's used as part of a cluster of PXE booting Raspberry Pi's which take input video and transcode them to generate streams in HTTP Live Streaming format.



Files
------

* `get_jobs.py` acts as the service runner, and is essentially a wrapper script for [HLS Stream Creator](https://github.com/bentasker/HLS-Stream-Creator). It retrieves jobs from C&C, calculates output bitrates and then generates a HLS-Stream-Creator process.
* `hls-muxer.service` is the `Systemd` unit file used to have the service start at boot
* `muxlogs` is config for `logrotate` so that output logs are rotated daily