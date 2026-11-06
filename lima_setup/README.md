# Lima Setup for Apple Silicon

This section explains how to create an Ubuntu VM on Apple silicon Macs, install Falco and Docker in it, deploy the TraceJoin rules, and connect the VM to your Mac. This is the setup the [live telemetry and Gate 1](../README.md#live-telemetry-and-gate-1) path needs.

If you are unsure what processor your Apple machine is running, you can find out by clicking the Apple icon in the upper left and choosing "About this Mac". The first item listed, **Chip**, tells you what silicon you're running on.

**Tested with:** Lima 2.2.1, Ubuntu 26.04 (arm64), Falco 0.45.0, and Docker 29.9.0 with Compose 5.6.0.

**Status:** these steps were checked against a working VM and its install history, but haven't been run start to finish on a clean VM yet. The Falco steps are adapted from the [Falco docs](https://falco.org/docs/).

## Prerequisites

Install Homebrew according to the project's documentation. Keep this repo somewhere under your home folder: Lima mounts it read-only in the VM, and the deploy step copies the rules from there.

## Create the VM

Run these on your Mac.

1.  **Install Lima**
    Use Homebrew to install Lima.
    ```bash
    brew install lima
    ```

2.  **Create an Ubuntu VM with Lima**
    The `--set` line forwards the VM's Docker socket to your Mac. Keep the name `falco-quickstart`, because `inspect_sandbox/scripts/06_join_telemetry.py` reads Falco's log from a VM with that name.
    ```bash
    limactl start --name=falco-quickstart \
      --set '.portForwards += [{"guestSocket":"/var/run/docker.sock","hostSocket":"{{.Dir}}/sock/docker.sock"}]' \
      template:ubuntu-lts
    ```

3.  **Shell into the Ubuntu VM**
    Once the VM is started, shell into it to begin the Falco installation.
    ```bash
    limactl shell falco-quickstart
    ```

---

## Install Falco

Run these inside the VM.

### Set up the package repository

1.  **Add the Falco repository key.**
    ```bash
    curl -fsSL https://falco.org/repo/falcosecurity-packages.asc | \
    sudo gpg --dearmor -o /usr/share/keyrings/falco-archive-keyring.gpg
    ```

2.  **Add the Falco repository.**
    ```bash
    sudo bash -c 'cat << EOF > /etc/apt/sources.list.d/falcosecurity.list
    deb [signed-by=/usr/share/keyrings/falco-archive-keyring.gpg] https://download.falco.org/packages/deb stable main
    EOF'
    ```

3.  **Read the repository contents.**
    ```bash
    sudo apt-get update -y
    ```

### Install dialog

Install `dialog`, which is used by the Falco installer.
```bash
sudo apt-get install -y dialog
```

### Install Falco

1.  **Install the latest Falco version.**
    ```bash
    sudo apt-get install -y falco
    ```

2.  **Configure the Driver**
    When prompted, choose the **Modern eBPF** option. This will enable the usage of the modern eBPF-based driver.

3.  **Configure Automatic Updates**
    When prompted, choose **Yes**. This option allows Falco to update its rules automatically.

4.  **Wait for completion**
    Wait for the Falco installation to complete - this should only take a few minutes.

### Verify Falco is running

Make sure the Falco service is running. The output should say `Active: active (running)`.
```bash
sudo systemctl status falco-modern-bpf.service
```

### See Falco in action

There is a Falco rule designed to trigger whenever someone accesses a sensitive file (such as `/etc/shadow`). Trigger it, then look for a `Sensitive file opened for reading` warning in the system log:

```bash
sudo cat /etc/shadow > /dev/null
sudo journalctl _COMM=falco -p warning
```

---

## Install Docker

Run these inside the VM. They install Docker from its own repository, not Ubuntu's `docker.io` package.

1.  **Add the Docker repository key.**
    ```bash
    sudo install -m 0755 -d /etc/apt/keyrings
    sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    sudo chmod a+r /etc/apt/keyrings/docker.asc
    ```

2.  **Add the Docker repository.**
    ```bash
    sudo tee /etc/apt/sources.list.d/docker.sources > /dev/null <<EOF
    Types: deb
    URIs: https://download.docker.com/linux/ubuntu
    Suites: $(. /etc/os-release && echo "$VERSION_CODENAME")
    Components: stable
    Architectures: $(dpkg --print-architecture)
    Signed-By: /etc/apt/keyrings/docker.asc
    EOF
    ```

3.  **Install Docker.**
    ```bash
    sudo apt-get update -y
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    ```

4.  **Give your user access to Docker, then leave the VM.**
    ```bash
    sudo usermod -aG docker "$USER"
    exit
    ```

5.  **Restart the VM from your Mac**, so the socket forward picks up the new group.
    ```bash
    limactl stop falco-quickstart
    limactl start falco-quickstart
    ```

---

## Connect your Mac to Docker

Run these on your Mac. You don't need Docker Desktop.

1.  **Install the Docker client and Compose.**
    ```bash
    brew install docker docker-compose
    ```

2.  **Tell Docker where Compose is.** Add this key to `~/.docker/config.json`, creating the file if it doesn't exist.
    ```json
    {
      "cliPluginsExtraDirs": ["/opt/homebrew/lib/docker/cli-plugins"]
    }
    ```

3.  **Point the client at the VM.**
    ```bash
    docker context create lima-falco --docker "host=unix://$HOME/.lima/falco-quickstart/sock/docker.sock"
    docker context use lima-falco
    ```

4.  **Check the connection.** `docker version` should show both a Client and a Server section.
    ```bash
    docker version
    docker compose version
    ```

---

## Deploy the TraceJoin rules

From the repo root on your Mac, open a shell in the VM. It starts in the same folder.

```bash
limactl shell falco-quickstart
```

Then, inside the VM:

```bash
sudo mkdir -p /etc/falco/rules.d /etc/falco/config.d /var/log/falco
sudo cp detections/tracejoin_rules.yaml /etc/falco/rules.d/
sudo cp detections/falco-output.yaml /etc/falco/config.d/tracejoin-output.yaml
sudo falco --validate /etc/falco/rules.d/tracejoin_rules.yaml
sudo systemctl restart falco-modern-bpf
exit
```

The validate step should report `"successful":true`. Repeat this section whenever either file changes.

You can now run Gate 1 from the [main README](../README.md#live-telemetry-and-gate-1).

---

## Everyday use

- Start the VM before a live run with `limactl start falco-quickstart`, and stop it with `limactl stop falco-quickstart`. Docker and Falco start with the VM.
- Sleeping your Mac pauses the VM, so Falco's timestamps fall behind. You don't need to correct this: the join measures the offset for each run.

## Cleanup

### Remove the Lima virtual machine

If you wish to remove the Lima virtual machine and its Docker context:

```bash
docker context use default
docker context rm lima-falco
limactl delete falco-quickstart --force
```
