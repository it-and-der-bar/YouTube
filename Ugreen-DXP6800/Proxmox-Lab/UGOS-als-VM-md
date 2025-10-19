# UGOS Original-Software als VM unter Proxmox

Diese Dokumentation beschreibt **recht oberflächlig** die Vorgehensweise, um die originale **UGOS**-Software als virtuelle Maschine (VM) in **Proxmox** zu betreiben. Die **Bootloader-Anpassungen** wurden mit einem **Ubuntu 24 Live**-System durchgeführt.

> - Ausgangslage ist ein frisch installiertes Proxmox 9, auf einer ext. HDD

---

## 1) Quell-SSD nach QCOW2 konvertieren
Leider ist es mir nicht geglückt die NVME direkt in ein LVM Volume zu klonen.

```bash
qemu-img convert -p -O qcow2 -c /dev/nvme0n1 /root/nvme0n1.qcow2
```

---

## 2) QCOW2 als Virtuelle Disk in Proxmox-VM importieren

VM-ID im Beispiel: **901**, Storage: **local-lvm**, Format: **raw**

```bash
qm importdisk 901 /root/nvme0n1.qcow2 local-lvm --format raw
```

---

## 3) Bootloader-Anpassungen (aus Ubuntu 24 Live)

### 3.1 ESP (EFI System Partition) einbinden
```bash
mount /dev/sda1 /mnt/esp
#   mount /dev/sda3 /mnt/root
```

### 3.2 Bootloader-Dateien bereitstellen
```bash
mkdir -p /mnt/esp/EFI/BOOT
cp -f /mnt/esp/EFI/debian/grubx64.efi /mnt/esp/EFI/BOOT/BOOTX64.EFI
```

### 3.3 Root-UUID ermitteln
```bash
UUID=$(blkid -s UUID -o value /dev/sda3)
```

### 3.4 GRUB-Konfiguration für BOOTX64.EFI schreiben
```bash
cat > /mnt/esp/EFI/BOOT/grub.cfg <<EOF
search --no-floppy --fs-uuid --set=root $UUID
set prefix=($root)/boot/grub
configfile \$prefix/grub.cfg
EOF
sync
```

---

## Hinweise

- Die Befehle wurden **unverändert** übernommen (keine zusätzlichen Schritte).  
- Die **Partitionen** im Beispiel: `/dev/sda1` (ESP), `/dev/sda3` (Root).  
- Die Anpassungen erfolgten aus einer **Ubuntu 24 Live**-Umgebung.

