# UGOS Original-Software als VM unter Proxmox

Diese Dokumentation beschreibt **recht oberflächlig** die Vorgehensweise, um die originale **UGOS**-Software als virtuelle Maschine (VM) in **Proxmox** zu betreiben. Die **Bootloader-Anpassungen** wurden mit einem **Ubuntu 24 Live**-System durchgeführt.

> - Ausgangslage ist ein frisch installiertes Proxmox 9, auf einer ext. HDD
> - kpartx installiert (apt install kpartx)
---

## 1) Quell-SSD nach QCOW2 konvertieren
Leider ist es mir nicht geglückt die NVME direkt in ein LVM Volume zu klonen.

```bash
qemu-img convert -p -O qcow2 -c /dev/nvme0n1 /root/nvme0n1.qcow2
```

---
## 2) VM anlegen und QCOW2 als Virtuelle Disk in Proxmox-VM importieren

VM-ID im Beispiel: **901**, Storage: **local-lvm**, Format: **raw**
---
## 2b) VM 901 anlegen
```bash
qm create 901 \
  --name ugos \
  --machine q35 \
  --bios ovmf \
  --ostype l26 \
  --memory 4096 \
  --balloon 0 \
  --sockets 2 \
  --cores 2 \
  --scsihw virtio-scsi-pci
qm set 901 --efidisk0 local-lvm:0,efitype=4m,pre-enrolled-keys=0
qm config 901

qm importdisk 901 /root/nvme0n1.qcow2 local-lvm --format raw
qm set 901 --boot order=sata0
```
---



## 3) Bootloader-Anpassungen (aus Ubuntu 24 Live)

### 3.1 ESP (EFI System Partition) einbinden
```bash
kpartx -av  /dev/pve/vm-901-disk-1
mkdir /mnt/pve-vm--901--disk--1p1
mount /dev/mapper/pve-vm--901--disk--1p1 /mnt/pve-vm--901--disk--1p1
```

### 3.2 Bootloader-Dateien bereitstellen
```bash
mkdir -p /mnt/pve-vm--901--disk--1p1/EFI/BOOT
cp -f /mnt/pve-vm--901--disk--1p1/EFI/debian/grubx64.efi /mnt/pve-vm--901--disk--1p1/EFI/BOOT/BOOTX64.EFI
```

### 3.3 Root-UUID ermitteln /  GRUB-Konfiguration für BOOTX64.EFI schreiben
```bash
UUID=$(blkid -s UUID -o value /dev/mapper/pve-vm--901--disk--1p3)
cat > /mnt/pve-vm--901--disk--1p1/EFI/BOOT/grub.cfg <<EOF
search --no-floppy --fs-uuid --set=root $UUID
set prefix=($root)/boot/grub
configfile \$prefix/grub.cfg
EOF
sync
```

### 3.4 sauber unmounten
```bash
umount /mnt/pve-vm--901--disk--1p1
kpartx -l /dev/pve/vm-901-disk-1
rm -r /mnt/pve-vm--901--disk--1p1
```

## one shot
```bash
#vm erzeugen
qm create 901 \
  --name ugos \
  --machine q35 \
  --bios ovmf \
  --ostype l26 \
  --memory 4096 \
  --balloon 0 \
  --sockets 2 \
  --cores 2 \
  --scsihw virtio-scsi-pci
qm set 901 --efidisk0 local-lvm:0,efitype=4m,pre-enrolled-keys=0
qm config 901

#disk import
qm importdisk 901 /root/nvme0n1.qcow2 local-lvm --format raw
qm set 901 --sata0 local-lvm:vm-901-disk-1
qm set 901 --boot order=sata0

#bootloader anpassen
kpartx -av  /dev/pve/vm-901-disk-1
mkdir /mnt/pve-vm--901--disk--1p1
mount /dev/mapper/pve-vm--901--disk--1p1 /mnt/pve-vm--901--disk--1p1
mkdir -p /mnt/pve-vm--901--disk--1p1/EFI/BOOT
cp -f /mnt/pve-vm--901--disk--1p1/EFI/debian/grubx64.efi /mnt/pve-vm--901--disk--1p1/EFI/BOOT/BOOTX64.EFI
UUID=$(blkid -s UUID -o value /dev/mapper/pve-vm--901--disk--1p3)
cat > /mnt/pve-vm--901--disk--1p1/EFI/BOOT/grub.cfg <<EOF
search --no-floppy --fs-uuid --set=root $UUID
set prefix=($root)/boot/grub
configfile \$prefix/grub.cfg
EOF
sync

#aufräumen
umount /mnt/pve-vm--901--disk--1p1
kpartx -dv /dev/pve/vm-901-disk-1
rm -r /mnt/pve-vm--901--disk--1p1

```

---

## Hinweise

- Die Befehle wurden **unverändert** übernommen (keine zusätzlichen Schritte).  
- Die **Partitionen** im Beispiel: `/dev/sda1` (ESP), `/dev/sda3` (Root).  
- Die Anpassungen erfolgten aus einer **Ubuntu 24 Live**-Umgebung.

