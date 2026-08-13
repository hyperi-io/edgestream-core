#!/bin/bash

# Hardened EdgeStream Gateway Configuration Tool
# Replaces global IFS tampering with local scoping and modern bash syntax

PATH=/opt/edgestream-core/bin:${PATH}

# Globals
program_name=$(basename "$0")
MENU_TITLE="EdgeStream Gateway Configuration Tool (edgestream-config)"

if [[ $EUID -ne 0 ]]; then
   echo "This script must be run as root" 1>&2
   exit 1
fi

calc_wt_size() {
  WT_HEIGHT=16
  WT_WIDTH=$(tput cols)

  if [ -z "$WT_WIDTH" ] || [ "$WT_WIDTH" -lt 60 ]; then
    WT_WIDTH=80
  fi
  if [ "$WT_WIDTH" -gt 178 ]; then
    WT_WIDTH=120
  fi
  WT_MENU_HEIGHT=$((WT_HEIGHT-10))
}

do_about() {
  local version_info
  version_info=$(dpkg -s edgestream-core 2> /dev/null | grep Version)
  whiptail --msgbox "Base configuration tool for EdgeStream Gateway.\n\n$version_info" 20 70 1
  return 0
}

is_ipv4_address() {
  local ip="$1"
  local stat=1
  # Regex check for basic IPv4 structure
  if [[ $ip =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
    # HARDENING: Use local IFS within read to avoid global tampering
    local ip_parts
    IFS='.' read -r -a ip_parts <<< "$ip"
    
    [[ ${ip_parts[0]} -le 255 && ${ip_parts[1]} -le 255 \
        && ${ip_parts[2]} -le 255 && ${ip_parts[3]} -le 255 ]]
    stat=$?
  fi
  return "$stat"
}

is_ipv6_address() {
  if [[ $1 =~ ^([0-9a-fA-F]{0,4}:){0,7}[0-9a-fA-F]{1,4}$ ]]; then
    return 0
  fi
  return 1
}

get_hostname() {
  pyedgestream system --get-hostname
}

set_hostname() {
  local new_name="${1:-0}"
  pyedgestream system --set-hostname "$new_name"
}

do_hostname() {
  local current_host
  local new_host
  current_host=$(get_hostname)
  new_host=$(whiptail --inputbox "Please enter a hostname" 20 60 "$current_host" 3>&1 1>&2 2>&3)
  if [ "$?" -eq 0 ]; then
    set_hostname "$new_host"
  fi
}

get_org_id() {
  pyedgestream system --get-org-id
}

set_org_id() {
  local new_id="${1:-0}"
  pyedgestream system --set-org-id "$new_id"
}

del_mgmt_intf() {
  local func_out
  func_out=$(pyedgestream network --del-mgmt-intf)
  echo "del_mgmt_intf: $func_out" >> /tmp/edgestream-config.log
}

do_org_id() {
  local current_id
  local new_id
  current_id=$(get_org_id)
  new_id=$(whiptail --inputbox "Please enter a org id" 20 60 "$current_id" 3>&1 1>&2 2>&3)
  if [ "$?" -eq 0 ]; then
    set_org_id "$new_id"
  fi
}

get_site_id() {
  pyedgestream system --get-site-id
}

set_site_id() {
  local new_id="${1:-0}"
  pyedgestream system --set-site-id "$new_id"
}

do_site_id() {
  local current_id
  local new_id
  current_id=$(get_site_id)
  new_id=$(whiptail --inputbox "Please enter a site id" 20 60 "$current_id" 3>&1 1>&2 2>&3)
  if [ "$?" -eq 0 ]; then
    set_site_id "$new_id"
  fi
}

do_finish() {
  if [ "${ASK_TO_REBOOT-0}" -eq 1 ]; then
    if whiptail --yesno "Would you like to reboot now?" 20 60 2; then
      sync
      reboot
    fi
  fi
  exit 0
}

do_system_menu() {
  while true; do
    local fun
    fun=$(whiptail --title "$MENU_TITLE" --menu "System Options" "$WT_HEIGHT" "$WT_WIDTH" "$WT_MENU_HEIGHT" --cancel-button Back --ok-button Select \
      "S1 Org ID" "Set organization id for this edgestream" \
      "S2 Site ID" "Set site id for this network" \
      "S3 Hostname" "Set name for this edgestream" \
      3>&1 1>&2 2>&3)
    
    local ret=$?
    if [ $ret -eq 1 ]; then
      return 0
    elif [ $ret -eq 0 ]; then
      case "$fun" in
        S1\ *) do_org_id ;;
        S2\ *) do_site_id ;;
        S3\ *) do_hostname ;;
        *) whiptail --msgbox "Programmer error: unrecognized option" 20 60 1 ;;
      esac || whiptail --msgbox "There was an error running option $fun" 20 60 1
    fi
  done
}

get_management_device() {
  pyedgestream network --get-interfaces
}

get_event_device() {
  pyedgestream network --get-interfaces
}

get_management_interface() {
  pyedgestream network --get-mgmt-intf
}

get_event_interface() {
  pyedgestream network --get-event-intf
}

get_dns_servers() {
  pyedgestream network --get-dns
}

get_ntp_servers() {
  pyedgestream network --get-ntp
}

do_save_mgmt_interface() {
  local device="$1"
  local ip_addr="$2"
  local mask="$3"
  local gw="$4"

  {
    echo "Saving Mgmt: $device"
    echo "IP: $ip_addr"
    echo "Mask: $mask"
    echo "GW: $gw"
  } >> /tmp/edgestream-config.log

  if [ -z "$ip_addr" ] ; then
    if whiptail --yesno --title "Save settings" "Remove management interface settings? Will default to DHCP.\n\nWARNING: Remote connections may be terminated." 0 0 --yes-button "Update" --no-button "Cancel"; then
      pyedgestream network --del-event-intf
    fi
    return 0
  fi

  if whiptail --yesno --title "Save settings" "Management interface settings updated.\n\nWARNING: Remote connections may be terminated." 0 0 --yes-button "Update" --no-button "Cancel"; then
    pyedgestream network --set-mgmt-intf --mgmt-device "${device:-eth0}" --mgmt-ip-address "$ip_addr" --mgmt-netmask "${mask:-255.255.0.0}" --mgmt-gateway "${gw:-0.0.0.0}"
  fi
}

join_arr() {
  local delimiter="$1"
  shift
  local items=("$@")
  local out
  
  # Join array using printf and substring removal
  printf -v out "%s${delimiter}" "${items[@]}"
  echo "${out%${delimiter}}"
}

do_save_dns_servers() {
  local dns_servers=("$@")
  
  if whiptail --yesno --title "Save settings" "DNS server settings updated. Apply?" 0 0 --yes-button "Update" --no-button "Cancel"; then
    if [ ${#dns_servers[@]} -eq 0 ]; then
      pyedgestream network --delete-all-dns
    else
      local dns_cli_list
      dns_cli_list=$(join_arr , "${dns_servers[@]}")
      pyedgestream network --set-dns "$dns_cli_list"
    fi
  fi
}

do_save_ntp_servers() {
  local ntp_servers=("$@")
  
  if whiptail --yesno --title "Save settings" "NTP server settings updated. Apply?" 0 0 --yes-button "Update" --no-button "Cancel"; then
    if [ ${#ntp_servers[@]} -eq 0 ]; then
      pyedgestream network --delete-all-ntp
    else
      local ntp_cli_list
      ntp_cli_list=$(join_arr , "${ntp_servers[@]}")
      pyedgestream network --set-ntp "$ntp_cli_list"
    fi
  fi
}

do_save_event_interface() {
  local device="$1"
  local ip="$2"
  local mask="$3"
  local gw="$4"

  if whiptail --yesno --title "Save settings" "Event interface settings updated. Apply?" 0 0 --yes-button "Update" --no-button "Cancel"; then
    pyedgestream network --set-event-intf --event-device "$device" --event-ip-address "$ip" --event-netmask "$mask" --event-gateway "$gw"
  fi
}

do_mgmt_device() {
  local active_dev="$1"
  local mgmt_devices
  # HARDENING: Set IFS locally for the read
  IFS=' ' read -r -a mgmt_devices <<< "$(get_management_device)"

  local whiptail_args=(
    --title "$MENU_TITLE"
    --notags
    --radiolist "Select Management Interface Device:"
    10 80 "${#mgmt_devices[@]}"
  )

  local i=0
  for item in "${mgmt_devices[@]}"; do
    whiptail_args+=( "$((++i))" "$item" )
    [[ $item = "$active_dev" ]] && whiptail_args+=( "on" ) || whiptail_args+=( "off" )
  done

  local fun
  fun=$(whiptail "${whiptail_args[@]}" 3>&1 1>&2 2>&3)
  if [ "$?" -eq 0 ]; then
    echo "${mgmt_devices[$fun-1]}"
  fi
}

do_event_device() {
  local active_dev="$1"
  local event_devices
  IFS=' ' read -r -a event_devices <<< "$(get_event_device)"

  local whiptail_args=(
    --title "$MENU_TITLE"
    --notags
    --radiolist "Select Event Interface Device:"
    10 80 "${#event_devices[@]}"
  )

  local i=0
  for item in "${event_devices[@]}"; do
    whiptail_args+=( "$((++i))" "$item" )
    [[ $item = "$active_dev" ]] && whiptail_args+=( "on" ) || whiptail_args+=( "off" )
  done

  local fun
  fun=$(whiptail "${whiptail_args[@]}" 3>&1 1>&2 2>&3)
  if [ "$?" -eq 0 ]; then
    echo "${event_devices[$fun-1]}"
  fi
}

do_generic_textbox() {
  local title="$1"
  local value="$2"
  local fun
  fun=$(whiptail --inputbox "$title" 20 60 "$value" 3>&1 1>&2 2>&3)
  [ "$?" -eq 0 ] && echo "$fun"
}

do_generic_yesnobox() {
  whiptail --yesno "$1" 20 60 --yes-button Delete --no-button Cancel 3>&1 1>&2 2>&3
}

do_generic_errorbox() {
  whiptail --title "$1" --msgbox "$2" 20 60 3>&1 1>&2 2>&3
}

do_event_interface() {
  local event_data
  IFS=' ' read -r -a event_data <<< "$(get_event_interface)"
  
  local config_dev="${event_data[0]}"
  local config_ip="${event_data[3]}"
  local config_mask="${event_data[4]}"
  local config_gw="${event_data[5]}"

  local curr_dev="$config_dev"
  local curr_ip="$config_ip"
  local curr_mask="$config_mask"
  local curr_gw="$config_gw"

  while true; do
    local fun
    fun=$(whiptail --title "$MENU_TITLE" --menu "Event Interface" "$WT_HEIGHT" "$WT_WIDTH" "$WT_MENU_HEIGHT" --cancel-button Back --ok-button Select \
      "E1 Interface"    "$curr_dev" \
      "E2 IP Address  " "$curr_ip" \
      "E3 Netmask"      "$curr_mask" \
      "E4 Gateway"      "$curr_gw" \
      "E5 Remove"       "Delete event interface settings" \
      3>&1 1>&2 2>&3)
    
    local ret=$?
    if [ $ret -eq 1 ]; then
      # Check for changes
      if [[ "$curr_dev" != "$config_dev" || "$curr_ip" != "$config_ip" || "$curr_mask" != "$config_mask" || "$curr_gw" != "$config_gw" ]]; then
        do_save_event_interface "$curr_dev" "$curr_ip" "$curr_mask" "$curr_gw"
      fi
      return 0
    elif [ $ret -eq 0 ]; then
      case "$fun" in
        E1\ *) curr_dev=$(do_event_device "$curr_dev") ;;
        E2\ *) 
           local val; val=$(do_generic_textbox "Enter Event Interface IP Address:" "$curr_ip")
           if [[ -n "$val" ]] && (is_ipv4_address "$val" || is_ipv6_address "$val"); then curr_ip="$val"; else do_generic_errorbox "Error" "Invalid IP"; fi ;;
        E3\ *)
           local val; val=$(do_generic_textbox "Enter Event Interface Netmask:" "$curr_mask")
           if [[ -n "$val" ]] && is_ipv4_address "$val"; then curr_mask="$val"; else do_generic_errorbox "Error" "Invalid Netmask"; fi ;;
        E4\ *)
           local val; val=$(do_generic_textbox "Enter Event Default Gateway:" "$curr_gw")
           if [[ -n "$val" ]] && (is_ipv4_address "$val" || is_ipv6_address "$val"); then curr_gw="$val"; else do_generic_errorbox "Error" "Invalid Gateway"; fi ;;
        E5\ *) do_generic_yesnobox "Delete Event Interface Settings?" && pyedgestream network --del-event-intf ;;
      esac
    fi
  done
}

do_management_interface() {
  local mgmt_data
  IFS=' ' read -r -a mgmt_data <<< "$(get_management_interface)"
  
  local config_dev="${mgmt_data[0]}"
  local config_ip="${mgmt_data[3]}"
  local config_mask="${mgmt_data[4]}"
  local config_gw="${mgmt_data[5]}"

  local curr_dev="$config_dev"
  local curr_ip="$config_ip"
  local curr_mask="$config_mask"
  local curr_gw="$config_gw"

  while true; do
    local fun
    fun=$(whiptail --title "$MENU_TITLE" --menu "Management Interface" "$WT_HEIGHT" "$WT_WIDTH" "$WT_MENU_HEIGHT" --cancel-button Back --ok-button Select \
      "M1 Interface"    "$curr_dev" \
      "M2 IP Address  " "$curr_ip" \
      "M3 Netmask"      "$curr_mask" \
      "M4 Gateway"      "$curr_gw" \
      "M5 Remove"       "Delete mgmt interface settings" \
      3>&1 1>&2 2>&3)

    if [ "$?" -ne 0 ]; then
      if [[ "$curr_dev" != "$config_dev" || "$curr_ip" != "$config_ip" || "$curr_mask" != "$config_mask" || "$curr_gw" != "$config_gw" ]]; then
        do_save_mgmt_interface "$curr_dev" "$curr_ip" "$curr_mask" "$curr_gw"
      fi
      return 0
    fi

    case "$fun" in
      M1\ *) curr_dev=$(do_mgmt_device "$curr_dev") ;;
      M2\ *) 
         local val; val=$(do_generic_textbox "Enter IP Address:" "$curr_ip")
         if [[ -n "$val" ]] && (is_ipv4_address "$val" || is_ipv6_address "$val"); then curr_ip="$val"; else do_generic_errorbox "Error" "Invalid IP"; fi ;;
      M3\ *)
         local val; val=$(do_generic_textbox "Enter Netmask:" "$curr_mask")
         if [[ -n "$val" ]] && is_ipv4_address "$val"; then curr_mask="$val"; else do_generic_errorbox "Error" "Invalid Netmask"; fi ;;
      M4\ *)
         local val; val=$(do_generic_textbox "Enter Gateway:" "$curr_gw")
         if [[ -n "$val" ]] && (is_ipv4_address "$val" || is_ipv6_address "$val"); then curr_gw="$val"; else do_generic_errorbox "Error" "Invalid Gateway"; fi ;;
      M5\ *) do_generic_yesnobox "Delete Management Interface Settings?" && del_mgmt_intf ;;
    esac
  done
}

do_interface_menu() {
  while true; do
    local fun
    fun=$(whiptail --title "$MENU_TITLE" --menu "Interface Options" "$WT_HEIGHT" "$WT_WIDTH" "$WT_MENU_HEIGHT" --cancel-button Back --ok-button Select \
      "I1 Management Interface" "Configure management interface network settings" \
      "I2 Event Interface" "Configure event interface network settings" \
      3>&1 1>&2 2>&3)
    [ "$?" -ne 0 ] && return 0
    case "$fun" in
      I1\ *) do_management_interface ;;
      I2\ *) do_event_interface ;;
    esac
  done
}

clean_array() {
  local target="$1"
  shift
  local array=("$@")
  local cleaned=()
  for item in "${array[@]}"; do
    [[ "$item" != "$target" ]] && cleaned+=("$item")
  done
  echo "${cleaned[@]}"
}

do_dns_menu() {
  local dns_list
  IFS=' ' read -r -a dns_list <<< "$(get_dns_servers)"
  local config_list=("${dns_list[@]}")

  while true; do
    local fun
    fun=$(whiptail --title "$MENU_TITLE" --menu "DNS Servers:" "$WT_HEIGHT" "$WT_WIDTH" "$WT_MENU_HEIGHT" --cancel-button Back --ok-button Select \
      --notags "${dns_list[@]}" "A1" "Add DNS Server" 3>&1 1>&2 2>&3)
    
    if [ "$?" -ne 0 ]; then
      # Compare arrays
      if [[ "${dns_list[*]}" != "${config_list[*]}" ]]; then
        do_save_dns_servers "${dns_list[@]}"
      fi
      return 0
    fi

    if [[ "$fun" == "A1" ]]; then
      local val; val=$(do_generic_textbox "Enter DNS Server (IP:port):" "")
      [[ -n "$val" ]] && dns_list+=("$val")
    else
      if whiptail --yesno "Delete DNS Server: $fun ?" 20 60; then
        dns_list=($(clean_array "$fun" "${dns_list[@]}"))
      fi
    fi
  done
}

do_ntp_menu() {
  local ntp_list
  IFS=' ' read -r -a ntp_list <<< "$(get_ntp_servers)"
  local config_list=("${ntp_list[@]}")

  while true; do
    local fun
    fun=$(whiptail --title "$MENU_TITLE" --menu "NTP Servers:" "$WT_HEIGHT" "$WT_WIDTH" "$WT_MENU_HEIGHT" --cancel-button Back --ok-button Select \
      --notags "${ntp_list[@]}" "A1" "Add NTP Server" 3>&1 1>&2 2>&3)
    
    if [ "$?" -ne 0 ]; then
      if [[ "${ntp_list[*]}" != "${config_list[*]}" ]]; then
        do_save_ntp_servers "${ntp_list[@]}"
      fi
      return 0
    fi

    if [[ "$fun" == "A1" ]]; then
      local val; val=$(do_generic_textbox "Enter NTP Server (IP:port):" "")
      [[ -n "$val" ]] && ntp_list+=("$val")
    else
      if whiptail --yesno "Delete NTP Server: $fun ?" 20 60; then
        ntp_list=($(clean_array "$fun" "${ntp_list[@]}"))
      fi
    fi
  done
}

do_advanced_menu() {
  while true; do
    local fun
    fun=$(whiptail --title "$MENU_TITLE" --menu "Advanced Options" "$WT_HEIGHT" "$WT_WIDTH" "$WT_MENU_HEIGHT" --cancel-button Back --ok-button Select \
      "A1 DNS Settings" "Configure domain nameserver settings" \
      "A2 NTP Settings" "Configure network time settings" \
      3>&1 1>&2 2>&3)
    [ "$?" -ne 0 ] && return 0
    case "$fun" in
      A1\ *) do_dns_menu ;;
      A2\ *) do_ntp_menu ;;
    esac
  done
}

# Execution Entry Point
calc_wt_size
while true; do
  fun=$(whiptail --title "$MENU_TITLE" --backtitle "https://edgestream.io" --menu "Setup Options" "$WT_HEIGHT" "$WT_WIDTH" "$WT_MENU_HEIGHT" --cancel-button Finish --ok-button Select \
    "1 System Options" "Configure system settings" \
    "2 Interface Options" "Configure interface settings" \
    "3 Advanced Options" "Configure advanced network settings" \
    "4 About" "Information about this configuration tool" \
    3>&1 1>&2 2>&3)
    
  if [ "$?" -ne 0 ]; then
    do_finish
  fi

  case "$fun" in
    1\ *) do_system_menu ;;
    2\ *) do_interface_menu ;;
    3\ *) do_advanced_menu ;;
    4\ *) do_about ;;
  esac
done