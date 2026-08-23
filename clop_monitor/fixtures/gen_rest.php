<?php
function commas($n) { return number_format($n); }
$_SESSION = array('hideflags'=>1,'token_messages'=>'tok','token_myalliance'=>'tok','user_id'=>1,'alliance_id'=>12);

// news.php
$allnews = array(
  array('message'=>'The Solar Empire has raised tariffs on gemstones.','posted'=>'2026-08-23 18:00:00'),
  array('message'=>'Rustlung declared war on Vashti.','posted'=>'2026-08-23 12:00:00'),
);
ob_start(); include(__DIR__.'/_news_render.php'); file_put_contents(__DIR__.'/news.html', ob_get_clean());

// messages.php
$inbox = array(
  array('message'=>'want to trade copper? I can do 3100 a unit.','user_id'=>88,'username'=>'anon88','message_id'=>5,'sent'=>'2026-08-23 19:40:00'),
  array('message'=>'stop scouting my border.','user_id'=>91,'username'=>'Vashti','message_id'=>4,'sent'=>'2026-08-23 09:15:00'),
);
$sentbox = array(
  array('message'=>'sure, 3100 works.','user_id'=>88,'username'=>'anon88','message_id'=>3,'sent'=>'2026-08-23 19:45:00'),
);
ob_start(); include(__DIR__.'/_messages_render.php'); file_put_contents(__DIR__.'/messages.html', ob_get_clean());

// viewalliance.php
$allianceinfo = array('alliance_id'=>12,'name'=>'The Hoofprint','alliancerequested'=>0,'flag'=>'');
$useralliance = array('alliance_id'=>12);
$nationinfo = array('hideicons'=>0);
$display = array('flag'=>'');
$alliancemembers = array(
  array('user_id'=>88,'username'=>'anon88','stasismode'=>0,'flag'=>''),
  array('user_id'=>91,'username'=>'Vashti','stasismode'=>1,'flag'=>''),
);
$nations = array(
  88 => array(array('nation_id'=>47,'name'=>'Rustlung','region'=>2)),
  91 => array(array('nation_id'=>52,'name'=>'Saltmarch','region'=>3)),
);
$allianceresources = array('Copper'=>0,'Gems'=>0);
$allianceaffectedresources = array('Copper'=>300,'Gems'=>80);
$alliancerequiredresources = array('Copper'=>50,'Gems'=>10);
$affectedresources = array(); $requiredresources = array(); $resources = array();
ob_start(); include(__DIR__.'/_viewalliance_render.php'); file_put_contents(__DIR__.'/viewalliance.html', ob_get_clean());
echo "generated\n";
