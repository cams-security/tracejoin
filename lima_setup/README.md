# Lima Setup for Apple Silicon (M1/M2)

This section explains how to create an Ubuntu 24.04 VM on Apple computers running M1/M2/M3 silicon (as opposed to Intel).

If you are unsure what processor your Apple machine is running, you can find out by clicking the Apple icon in the upper left and choosing "About this Mac". The first item listed, **Chip**, tells you what silicon you're running on.

## Prerequisites

Install Homebrew according to the project's documentation.

## Create the VM

1.  **Install Lima**
    Use Homebrew to install Lima.
    ```bash
    brew install lima
    ```

2.  **Create an Ubuntu 24.04 VM with Lima**
    ```bash
    limactl start --name=falco-quickstart template:ubuntu-lts
    ```

3.  **Shell into the Ubuntu VM**
    Once the VM is started, shell into it to begin the Falco installation.
    ```bash
    limactl shell falco-quickstart
    ```

---

## Install Falco

Regardless of which setup you used above, this section will show you how to install Falco on a host system. You will begin by updating the package repository. Next, you'll install the `dialog` package. Then, you'll install Falco and ensure it's up and running.

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

    *Dialog window - Choose the modern eBPF driver*

3.  **Configure Automatic Updates**
    When prompted, choose **Yes**. Although we won't use the functionality in this exercise, this option allows Falco to update its rules automatically.

    *Dialog window - Choose the follow automatic ruleset updates*

4.  **Wait for completion**
    Wait for the Falco installation to complete - this should only take a few minutes.

### Verify Falco is running

Make sure the Falco service is running.
```bash
sudo systemctl status falco-modern-bpf.service
```

The output should be similar to the following:

```text
● falco-modern-bpf.service - Falco: Container Native Runtime Security with modern ebpf
     Loaded: loaded (/usr/lib/systemd/system/falco-modern-bpf.service; enabled; preset: enabled)
     Active: active (running) since Wed 2024-09-18 08:40:04 UTC; 11min ago
       Docs: https://falco.org/docs/
   Main PID: 4751 (falco)
      Tasks: 7 (limit: 2275)
     Memory: 24.7M (peak: 37.1M)
        CPU: 3.913s
     CGroup: /system.slice/falco-modern-bpf.service
             └─4751 /usr/bin/falco -o engine.kind=modern_ebpf

Sep 18 08:40:12 vagrant falco[4751]:    /etc/falco/falco.yaml
Sep 18 08:40:12 vagrant falco[4751]: System info: Linux version 6.8.0-31-generic (buildd@lcy02-amd64-080) (x86_64-linux-gnu-gcc-13 (Ubuntu 13.2.0-23ubuntu4) 13.2.0, GNU ld (GNU Binutils for Ubuntu) 2.42) #31-Ubuntu SMP PREEMPT_DYNAMIC Sat Apr 20 00:40:06 UTC 2024
Sep 18 08:40:12 vagrant falco[4751]: Loading rules from file /etc/falco/falco_rules.yaml
Sep 18 08:40:12 vagrant falco[4751]: Loading rules from file /etc/falco/falco_rules.local.yaml
Sep 18 08:40:12 vagrant falco[4751]: The chosen syscall buffer dimension is: 8388608 bytes (8 MBs)
Sep 18 08:40:12 vagrant falco[4751]: Starting health webserver with threadiness 2, listening on 0.0.0.0:8765
Sep 18 08:40:12 vagrant falco[4751]: Loaded event sources: syscall
Sep 18 08:40:12 vagrant falco[4751]: Enabled event sources: syscall
Sep 18 08:40:12 vagrant falco[4751]: Opening 'syscall' source with modern BPF probe.
Sep 18 08:40:12 vagrant falco[4751]: One ring buffer every '2' CPUs.
```

## See Falco in action

### Generate a suspicious event

There is a Falco rule designed to trigger whenever someone accesses a sensitive file (such as `/etc/shadow`). Run the following command to trigger that rule:

```bash
sudo cat /etc/shadow > /dev/null
```

### Examine Falco's output

One of the endpoints that Falco can write output to is `syslog`. You can examine the system logs using `journalctl` or by reading the log file directly.

#### Using `journalctl`

Run the following command to retrieve Falco messages that have been generated with a priority of **warning**:

```bash
sudo journalctl _COMM=falco -p warning
```

You should see output similar to the following:

```text
...
Sep 18 12:50:52 vagrant falco[4751]: 11:48:24.195279773: Warning Sensitive file opened for
reading by non-trusted program (file=/etc/shadow gparent=sudo ggparent=bash gggparent=sshd
evt_type=openat user=root user_uid=0 user_loginuid=1000 process=cat proc_exepath=/usr/bin/cat
parent=sudo command=cat /etc/shadow terminal=34818 container_id=host container_name=host)
...
```

#### Using `/var/log/syslog`

Log messages describing Falco's activity are also logged to syslog. Run the following command to retrieve Falco logs:

```bash
sudo grep Sensitive /var/log/syslog
```

You should see output similar to the following:

```text
...
2024-09-18T12:50:52.164570+00:00 vagrant falco: 11:48:24.195279773: Warning Sensitive file opened for
reading by non-trusted program (file=/etc/shadow gparent=sudo ggparent=bash gggparent=sshd
evt_type=openat user=root user_uid=0 user_loginuid=1000 process=cat proc_exepath=/usr/bin/cat
parent=sudo command=cat /etc/shadow terminal=34818 container_id=host container_name=host)
...
```

## Cleanup

### Remove the Lima virtual machine

If you wish to remove the Lima virtual machine:

```bash
limactl delete falco-quickstart --force
```
