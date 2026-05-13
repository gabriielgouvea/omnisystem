<?php
header('Content-Type: application/json');

// Horário de Brasília (UTC-3)
date_default_timezone_set('America/Sao_Paulo');

$dir = __DIR__ . '/outputs/';
$files = [];

foreach (glob($dir . '*.pdf') as $f) {
    $files[] = [
        'filename' => basename($f),
        'size'     => filesize($f),
        'saved_at' => date('d/m/Y H:i', filemtime($f)),
        'mtime'    => filemtime($f),
    ];
}

// Ordena pelo mais recente
usort($files, fn($a, $b) => $b['mtime'] - $a['mtime']);

// Remove o mtime da resposta
$files = array_map(fn($f) => ['filename' => $f['filename'], 'size' => $f['size'], 'saved_at' => $f['saved_at']], $files);

echo json_encode(['files' => $files]);
