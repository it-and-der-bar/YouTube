# Mail-Server-Interceptor – Test-Mailserver

Dieses Skript setzt einen kleinen Test-Mailserver auf, mit dem nachvollzogen werden kann, dass beim Einsatz von Outlook (new), Microsoft Verbindungsdaten eines **dritten** Mailservers auf seinen eigenen Systemen cached/speichert.  

---

## Wichtiger Hinweis – nicht produktiv nutzen!

- Dieser Mailserver ist **nicht** für den produktiven Einsatz gedacht.  
- Keine Härtung, kein Spam-Schutz, keine Security-Best Practices.  
- Einsatz ausschließlich zu **Test-, Demo- oder Schulungszwecken** in einer kontrollierten Umgebung.  
- Nutzung auf eigene Gefahr – es wird **keine Haftung** für Schäden oder Fehlkonfigurationen übernommen.

---

## Was das Skript macht

Kurz zusammengefasst:

- Installiert und konfiguriert:
  - **Postfix** (SMTP-Server)
  - **Dovecot** (IMAP-Server, mit Klartext-Authentifizierung für die Demo)
  - **stunnel4** (TLS-Terminator, transparenter Proxy für IMAPS/SMTPS)
  - **iptables-persistent**, **tcpdump** und **tshark** (für Routing & Analyse)
- Fragt Basisdaten ab (Hostname, Interface, Benutzername) und legt einen Mail-User mit zufälligem Passwort an.
- Richtet einen transparenten Proxy ein:
  - IMAPS: Port **993** → intern im Klartext auf Port **143**
  - SMTPS: Port **465** → intern im Klartext auf Port **25**
- Konfiguriert Policy-Routing und iptables-Regeln, um den Verkehr sauber umzuleiten.
- Startet die Dienste **nicht automatisch**, sondern gibt am Ende klare Hinweise aus:
  - Wie Postfix, Dovecot und stunnel manuell gestartet werden.
  - Welche Einstellungen im Mail-Client zu setzen sind.
  - Beispiel-Befehle für **tcpdump**, um den Klartext-Traffic (inkl. Zugangsdaten) auf `lo` mitzuschneiden.

# CLI Befehle

## IMAP Traffik inspizieren
````
tshark -i lo -l -d tcp.port==143,imap \
  -Y "tcp.port==143 && tcp.len>0 && imap" \
  -T fields \
  -e frame.time_relative -e ip.src -e tcp.srcport \
  -e ip.dst -e tcp.dstport -e imap.request -e imap.response \
  2>/dev/null \
| perl -ne '
    # nur Zeilen mit IMAP-Kommandos/Antworten durchlassen
    next unless /DONE|LOGIN|SELECT|FETCH|UID|STORE|SEARCH|LOGOUT|CAPABILITY|AUTHENTICATE|NOOP|IDLE|OK|BAD|NO/;
    # alle anderen IPv4-Adressen cyan
    s/(\d+\.\d+\.\d+\.\d+)/\e[1;36m$1\e[0m/g;
    # 127.x.x.x magenta
    s/(127\.\d+\.\d+\.\d+)/\e[1;35m$1\e[0m/g;
    # IMAP-Kommandos/Antworten gelb
    s/(DONE|LOGIN|SELECT|FETCH|UID|STORE|SEARCH|LOGOUT|CAPABILITY|AUTHENTICATE|NOOP|IDLE|OK|BAD|NO)/\e[1;33m$1\e[0m/g;
    print;
'
````

## SMTP Traffik inspizieren
````
tshark -i lo -l -d tcp.port==25,smtp \
  -Y "tcp.port==25 && tcp.len>0 && smtp" \
  -T fields \
  -e frame.time_relative -e ip.src -e tcp.srcport \
  -e ip.dst -e tcp.dstport \
  -e smtp.req.command -e smtp.req.parameter -e smtp.rsp.parameter -e smtp.auth.password -e smtp.auth.username \
  2>/dev/null \
| perl -MMIME::Base64 -we '
    use strict;
    use warnings;

    while (<STDIN>) {
        my $line = $_;

        # Nur Zeilen behalten, die SMTP-Kram ODER Base64 enthalten
        next unless $line =~ /
            HELO|EHLO|STARTTLS|MAIL|RCPT|DATA|RSET|NOOP|QUIT|AUTH|
            \b[245][0-9]{2}\b|
            [A-Za-z0-9+\/=]{8,}
        /x;

        # IPv4-Adressen cyan
        $line =~ s/(\d+\.\d+\.\d+\.\d+)/\e[1;36m$1\e[0m/g;

        # 127.x.x.x magenta (überschreibt das cyan)
        $line =~ s/(127\.\d+\.\d+\.\d+)/\e[1;35m$1\e[0m/g;

        # Base64-Blöcke suchen & decodiert anhängen
        $line =~ s{([A-Za-z0-9+\/=]{8,})}{
            my $b64 = $1;

            # Nur sinnvolle Base64-Längen decodieren
            if (length($b64) % 4 == 0) {
                my $decoded;
                eval { $decoded = decode_base64($b64) };

                # Nur druckbare ASCII-Strings anzeigen (mind. 3 Zeichen)
                if (defined $decoded && $decoded =~ /^[\x20-\x7e]{3,}$/) {
                    # z.B. dXNlcg==[user]
                    sprintf "%s[\e[1;32m%s\e[0m]", $b64, $decoded;
                } else {
                    $b64;
                }
            } else {
                $b64;
            }
        }ge;

        # SMTP-Kommandos & Statuscodes gelb
        $line =~ s/(HELO|EHLO|STARTTLS|MAIL|RCPT|DATA|RSET|NOOP|QUIT|AUTH|\b[245][0-9]{2}\b)/\e[1;33m$1\e[0m/g;

        print $line;
    }
'
````