<?php
function commas($n) { return number_format($n); }
$_SESSION = array('hideflags' => 1);
$nationinfo = array(
  'name' => 'Rustlung', 'regionname' => 'Zebrica', 'subregionname' => 'North ',
  'government' => 'Authoritarianism', 'economy' => 'Free Market',
  'user_id' => 88, 'username' => 'anon88', 'flag' => '',
  'alliance_id' => 12, 'alliance_name' => 'The Hoofprint',
  'creationdate' => '2026-05-01 12:00:00', 'age' => 143,
  'hideicons' => 0,
);
$display = array('description' => 'we trade fair.<br/>mostly.');
$displaygdp = commas(1875000);
$buildings = array('Advanced Factory' => 12, 'Gem Mine' => 8, 'Mechanized Copper Mine' => 20, 'Barracks' => 3);
$forcetypes = array(1=>"Cavalry",2=>"Tanks",3=>"Pegasi",4=>"Unicorns",5=>"Naval",6=>"Alicorns");
$attackers = array(
  array('forcegroup_id'=>7,'groupname'=>'Raiding Party','ownernation_id'=>91,'ownername'=>'Vashti',
        'ownerregionname'=>'Burrozil','name'=>'First Lance','type'=>4,'lowertype'=>'unicorns',
        'weapon_id'=>16,'armor_id'=>14,'weaponname'=>'Grid Squares','armorname'=>'Shining',
        'size'=>40,'training'=>12),
);
$defenders = array(
  array('forcegroup_id'=>3,'groupname'=>'Home Guard','ownernation_id'=>47,'ownername'=>'Rustlung',
        'ownerregionname'=>'Zebrica','name'=>'Wall Watch','type'=>3,'lowertype'=>'pegasi',
        'weapon_id'=>13,'armor_id'=>11,'weaponname'=>'Canopy Lights','armorname'=>'Dragon',
        'size'=>60,'training'=>6),
  array('forcegroup_id'=>3,'groupname'=>'Home Guard','ownernation_id'=>47,'ownername'=>'Rustlung',
        'ownerregionname'=>'Zebrica','name'=>'The Ascended','type'=>6,'lowertype'=>'alicorns',
        'weapon_id'=>0,'armor_id'=>0,'weaponname'=>'','armorname'=>'',
        'size'=>5,'training'=>20),
);
$affectedresources = array('Copper' => 100, 'Gems' => 40, 'Energy' => 0);
$requiredresources = array('Copper' => 20, 'Gems' => 0, 'Energy' => 65, 'Gasoline' => 10);
$resources = array('Copper' => 0, 'Gems' => 0, 'Energy' => 0);
$alliancerequiredresources = array(); $allianceaffectedresources = array(); $allianceresources = array();
ob_start();
include(__DIR__ . '/_viewnation_render.php');
$html = ob_get_clean();
file_put_contents(__DIR__ . '/viewnation_47.html', $html);
echo "wrote " . strlen($html) . " bytes\n";
